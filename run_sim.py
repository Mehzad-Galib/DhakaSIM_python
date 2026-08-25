#!/usr/bin/env python3
"""Route and demand generator -- writes ``path.txt`` and ``demand.txt``.

Reads the network from ``link.txt`` / ``node.txt``, runs Floyd-Warshall to get
shortest paths between every pair of boundary (single link) nodes, writes one
path and one demand row per ordered pair, then thins the demand down according
to ``DemandType``.  The full unthinned demand is kept as ``demand_all.txt``.

Files are read and written inside the selected network folder
(``input/<Network>/``), the same one the simulator itself uses, falling back to
``input/`` when no network is selected.  Run it from this folder::

    python run_sim.py                          # the network named in parameter.txt
    python run_sim.py --network kakrail_corridor  # override the selection
    python run_sim.py --paths-only             # regenerate path.txt, keep demand.txt
    python run_sim.py --force                  # allow overwriting survey demand

Only needed when the network geometry or ``DemandType`` changes; every shipped
network already has a matching ``path.txt`` and ``demand.txt``.

The survey-based networks derive ``demand.txt`` from real traffic counts, so it
is measured input rather than something this tool can recompute.  Those are
refused unless ``--force`` is given; use ``--paths-only`` to regenerate the
routes after a geometry edit while leaving the counts alone.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dhakasim.javacompat import JavaRandom, jround  # noqa: E402
from dhakasim.parameters import Parameters  # noqa: E402
from dhakasim.processor import Processor  # noqa: E402

INF = 99999


def output_dir() -> str:
    """The folder generated files belong in.

    Reads go through :meth:`Processor.input_path`, which falls back to
    ``input/`` for files a network folder does not override; writes must not
    fall back, or regenerating one network would clobber another's files.
    """
    if Parameters.NETWORK_DIR:
        candidate = os.path.join("input", Parameters.NETWORK_DIR)
        if os.path.isdir(candidate):
            return candidate
    return "input"


class Sim:
    LOW_RATE = 200
    MEDIUM_RATE = 700
    HIGH_RATE = 1200
    demand_type = 0  # 0 low; 1 mid; 2 high

    def __init__(self, network_override: str | None = None, paths_only: bool = False):
        self.out_node = []
        self.demand = 100
        self.adj_matrix = None
        self.path_matrix = None
        self.next = None
        self.rand = JavaRandom()
        self._network_override = network_override
        self._paths_only = paths_only

        self.out_path = ""
        self.out_demand = ""

        self._read_file()
        self._floyd_warshall()
        self._set_demand()

        i = 0
        for k in range(len(self.out_node)):
            for j in range(len(self.out_node)):
                if k == j:
                    continue

                src = self.out_node[k]
                dest = self.out_node[j]

                # A pair a one-way system cannot join gets no route -- an
                # unreachable destination would otherwise send _print_result
                # walking a next[][] chain that never arrives.
                if self.adj_matrix[src][dest] >= INF:
                    continue

                self._print_result(src, dest)
                # self._print_result(dest, src)

                i += 1
        self.out_path = f"{i}\n" + self.out_path
        self.out_demand = f"{i}\n" + self.out_demand

        # newline="" keeps the bare LF that Java's BufferedWriter.write and
        # PrintWriter.printf emit here (Python text mode would translate it to
        # the platform separator, unlike the CSV writers which mirror println)
        with open(os.path.join(output_dir(), "path.txt"), "w", newline="") as bw:
            bw.write(self.out_path)
        print(self.out_path)

        if self._paths_only:
            print(f"Wrote {os.path.join(output_dir(), 'path.txt')} "
                  f"({i} routes); demand.txt left untouched.")
            return

        with open(os.path.join(output_dir(), "demand.txt"), "w", newline="") as bw:
            bw.write(self.out_demand)
        print(self.out_demand)

        self._modify_demand()

    def _set_demand(self) -> int:
        i = len(self.out_node) - 1
        if len(self.out_node) * i > 100:
            if Sim.demand_type == 0:
                # low demand
                acceptable_node = jround(i / 3.0)
                self.demand = Sim.LOW_RATE // acceptable_node
            elif Sim.demand_type == 1:
                # medium demand
                acceptable_node = jround(2.0 * i / 3.0)
                self.demand = Sim.MEDIUM_RATE // acceptable_node
            elif Sim.demand_type == 2:
                # high demand
                acceptable_node = i
                self.demand = Sim.HIGH_RATE // acceptable_node
            else:
                acceptable_node = i
                self.demand = Sim.HIGH_RATE // acceptable_node
        else:
            acceptable_node = i
            if Sim.demand_type == 0:
                self.demand = Sim.LOW_RATE // acceptable_node
            elif Sim.demand_type == 1:
                self.demand = Sim.MEDIUM_RATE // acceptable_node
            elif Sim.demand_type == 2:
                self.demand = Sim.HIGH_RATE // acceptable_node
            else:
                self.demand = Sim.HIGH_RATE // acceptable_node
        return acceptable_node

    def my_sim(self) -> None:
        self._read_file()
        self._floyd_warshall()

        rand = JavaRandom()

        i = 0
        while len(self.out_node) > 1:
            s = rand.next_int_bound(len(self.out_node))
            d = rand.next_int_bound(len(self.out_node))

            if s == d:
                continue

            src = self.out_node[s]
            dest = self.out_node[d]

            self._print_result(src, dest)
            self._print_result(dest, src)

            if rand.next_int_bound(2) == 0:
                self.out_node.remove(src)
            else:
                self.out_node.remove(dest)

            i += 1
        self.out_path = f"{i * 2}\n" + self.out_path
        self.out_demand = f"{i * 2}\n" + self.out_demand

        with open(os.path.join(output_dir(), "path.txt"), "w", newline="") as bw:
            bw.write(self.out_path)
        print(self.out_path)

        with open(os.path.join(output_dir(), "demand.txt"), "w", newline="") as bw:
            bw.write(self.out_demand)
        print(self.out_demand)

    def _read_file(self) -> None:
        # parameter.txt first: it names the network, which decides where
        # link.txt and node.txt are read from.  It draws no random numbers, so
        # moving it ahead of the network read changes nothing else.
        self._read_parameters()

        # Each link's endpoints, read from link.txt itself: the up -> down
        # order is the link's own statement of direction, which is what a
        # one-way declaration in geometry.txt refers to.
        link_ends = []
        with open(Processor.input_path("link.txt"), "r") as f:
            num_of_links = int(f.readline())
            for _ in range(num_of_links):
                header = f.readline().split()
                link_ends.append((int(header[1]), int(header[2])))
                for _ in range(int(header[3])):
                    f.readline()

        # One-way links, so no route is generated against the direction the
        # road carries.  The simulator opens a one-way link's full width to
        # its up -> down direction and warns if demand runs both ways.
        oneway = set()
        try:
            with open(Processor.input_path("geometry.txt"), "r",
                      encoding="utf-8") as f:
                for line in f:
                    tokens = line.split("#", 1)[0].split()
                    if len(tokens) == 2 and tokens[0].lower() == "oneway":
                        oneway.add(int(tokens[1]))
        except OSError:
            pass

        with open(Processor.input_path("node.txt"), "r") as br:
            num_of_nodes = int(br.readline())

            temp_matrix = [[0] * num_of_nodes for _ in range(num_of_links)]

            for i in range(num_of_nodes):
                split = br.readline().rstrip("\n").split(" ")
                links = split[3:]

                for link in links:
                    temp_matrix[int(link)][i] = 1

                if len(links) == 1:
                    self.out_node.append(i)

            self.adj_matrix = [[INF] * num_of_nodes for _ in range(num_of_nodes)]
            self.path_matrix = [[0] * num_of_nodes for _ in range(num_of_nodes)]

            for i in range(num_of_nodes):
                self.adj_matrix[i][i] = 0

            for i in range(num_of_links):
                # up -> down from link.txt rather than the node listing:
                # identical for a two-way link (both directions get set),
                # and the only orientation a one-way link can be trusted in.
                row, col = link_ends[i]

                print(f"{row}, {col}")
                self.adj_matrix[row][col] = 1
                self.path_matrix[row][col] = i
                if i not in oneway:
                    self.adj_matrix[col][row] = 1
                    self.path_matrix[col][row] = i

    def _read_parameters(self) -> None:
        # parameter.txt is global, not per-network: Utilities.initialize reads
        # exactly this path, so resolving it through the network folder here
        # would let a stale copy inside input/<network>/ diverge from the
        # settings the simulator actually runs with.
        try:
            with open(os.path.join("input", "parameter.txt"), "r") as br:
                for data_line in br:
                    tokens = data_line.split()
                    if not tokens:
                        continue
                    name = tokens[0]
                    value = tokens[1]
                    if name.lower() == "demandtype":
                        Sim.demand_type = int(value)
                    elif name.lower() == "randomseed":
                        seed = int(value)
                        self.rand = JavaRandom() if seed < 0 else JavaRandom(seed)
                    elif name.lower() == "lowrate":
                        Sim.LOW_RATE = int(value)
                    elif name.lower() == "mediumrate":
                        Sim.MEDIUM_RATE = int(value)
                    elif name.lower() == "highrate":
                        Sim.HIGH_RATE = int(value)
                    elif name.lower() == "network":
                        # --network on the command line wins over the file
                        if self._network_override is None:
                            Parameters.NETWORK_DIR = value.strip()
        except OSError as e:
            print(e)
        if self._network_override is not None:
            Parameters.NETWORK_DIR = self._network_override

    def _floyd_warshall(self) -> None:
        n = len(self.adj_matrix)
        self.next = [[0] * n for _ in range(n)]

        for i in range(n):
            for j in range(n):
                if i != j:
                    self.next[i][j] = j

        for k in range(n):
            for i in range(n):
                for j in range(n):
                    if self.adj_matrix[i][k] + self.adj_matrix[k][j] < self.adj_matrix[i][j]:
                        self.adj_matrix[i][j] = (self.adj_matrix[i][k]
                                                + self.adj_matrix[k][j])
                        self.next[i][j] = self.next[i][k]

    def _print_result(self, src: int, dest: int) -> None:
        u = src
        v = dest
        self.out_path += f"{u} {v} "
        self.out_demand += f"{u} {v} "

        path = str(u)

        while True:
            u = self.next[u][v]
            path += " " + str(u)
            if u == v:
                break

        split = path.split(" ")
        path = ""

        for i in range(len(split) - 1):
            path += str(self.path_matrix[int(split[i])][int(split[i + 1])]) + " "

        self.out_path += path + "\n"
        self.out_demand += f"{self.demand}\n"

    def _modify_demand(self) -> None:
        i = len(self.out_node) - 1
        acceptable_node = self._set_demand()

        out = output_dir()
        count = 0
        limit = 0
        try:
            with open(os.path.join(out, "demand.txt"), "r") as br, \
                    open(os.path.join(out, "demand_mod.txt"), "w", newline="") as pw:
                limit = int(br.readline())
                sb = []
                for _ in range(limit):
                    s = br.readline().rstrip("\n")
                    if self.rand.next_int_bound(i) < acceptable_node:
                        sb.append(s + "\n")
                        count += 1
                pw.write(f"{count}\n")
                pw.write("".join(sb))
        except OSError as e:
            print(e)

        if os.path.exists(os.path.join(out, "demand_all.txt")):
            os.remove(os.path.join(out, "demand_all.txt"))
        os.replace(os.path.join(out, "demand.txt"), os.path.join(out, "demand_all.txt"))
        os.replace(os.path.join(out, "demand_mod.txt"), os.path.join(out, "demand.txt"))
        print(f"Wrote {os.path.join(out, 'path.txt')} and "
              f"{os.path.join(out, 'demand.txt')} ({count} routes kept of {limit}); "
              f"unthinned demand kept as {os.path.join(out, 'demand_all.txt')}.")


def _flag_value(argv, flag):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    network = _flag_value(argv, "--network")
    paths_only = "--paths-only" in argv
    force = "--force" in argv

    # Resolving the network needs parameter.txt, which Sim reads itself; do a
    # cheap pre-pass so the guard below can report the right folder before any
    # file is written.
    probe = Sim.__new__(Sim)
    probe.rand = JavaRandom()
    probe._network_override = network
    probe._read_parameters()
    out = output_dir()

    if not paths_only and not force:
        # demand.txt of a survey network comes from measured traffic counts, so
        # it is input data this tool cannot recompute -- refuse to replace it.
        survey = os.path.exists(os.path.join(out, "demand_by_hour.txt"))
        if survey:
            print(f"Refusing to overwrite {os.path.join(out, 'demand.txt')}: "
                  f"'{Parameters.NETWORK_DIR}' is a survey network and its demand "
                  f"comes from measured counts (demand_by_hour.txt is present).\n"
                  f"  --paths-only  regenerate path.txt only, keep the counts\n"
                  f"  --force       overwrite the measured demand anyway")
            return 1

    print(f"Network: {Parameters.NETWORK_DIR or '(none)'}  ->  writing into {out}/")
    try:
        Sim(network_override=network, paths_only=paths_only)
    except Exception:  # noqa: BLE001 - Java prints the stack trace and exits 0
        import traceback
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
