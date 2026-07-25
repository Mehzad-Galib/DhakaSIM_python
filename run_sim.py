#!/usr/bin/env python3
"""Route and demand generator -- writes ``input/path.txt`` and ``input/demand.txt``.

Reads the network from ``input/link.txt`` / ``input/node.txt``, runs
Floyd-Warshall to get shortest paths between every pair of boundary (single
link) nodes, writes one path and one demand row per ordered pair, then thins
the demand file down according to ``DemandType``.  The full unthinned demand is
kept as ``input/demand_all.txt``.

Only needed when the network or ``DemandType`` changes -- ``input/`` already
ships with matching ``path.txt`` and ``demand.txt``.  Run it from this folder::

    python run_sim.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dhakasim.javacompat import JavaRandom, jround  # noqa: E402

INF = 99999


class Sim:
    LOW_RATE = 200
    MEDIUM_RATE = 700
    HIGH_RATE = 1200
    demand_type = 0  # 0 low; 1 mid; 2 high

    def __init__(self):
        self.out_node = []
        self.demand = 100
        self.adj_matrix = None
        self.path_matrix = None
        self.next = None
        self.rand = JavaRandom()

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

                self._print_result(src, dest)
                # self._print_result(dest, src)

                i += 1
        self.out_path = f"{i}\n" + self.out_path
        self.out_demand = f"{i}\n" + self.out_demand

        # newline="" keeps the bare LF that Java's BufferedWriter.write and
        # PrintWriter.printf emit here (Python text mode would translate it to
        # the platform separator, unlike the CSV writers which mirror println)
        with open("input/path.txt", "w", newline="") as bw:
            bw.write(self.out_path)
        print(self.out_path)

        with open("input/demand.txt", "w", newline="") as bw:
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

        with open("input/path.txt", "w", newline="") as bw:
            bw.write(self.out_path)
        print(self.out_path)

        with open("input/demand.txt", "w", newline="") as bw:
            bw.write(self.out_demand)
        print(self.out_demand)

    def _read_file(self) -> None:
        with open("input/link.txt", "r") as f:
            num_of_links = int(f.readline())

        with open("input/node.txt", "r") as br:
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
                column = [temp_matrix[i][j] for j in range(num_of_nodes)]

                row = column.index(1) if 1 in column else -1
                col = len(column) - 1 - column[::-1].index(1) if 1 in column else -1

                print(f"{row}, {col}")
                self.adj_matrix[row][col] = 1
                self.adj_matrix[col][row] = 1

                self.path_matrix[row][col] = i
                self.path_matrix[col][row] = i

        try:
            with open("input/parameter.txt", "r") as br:
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
        except OSError as e:
            print(e)

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

        count = 0
        try:
            with open("input/demand.txt", "r") as br, \
                    open("input/demand_mod.txt", "w", newline="") as pw:
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

        if os.path.exists("input/demand_all.txt"):
            os.remove("input/demand_all.txt")
        os.replace("input/demand.txt", "input/demand_all.txt")
        os.replace("input/demand_mod.txt", "input/demand.txt")


def main() -> int:
    try:
        Sim()
    except Exception:  # noqa: BLE001 - Java prints the stack trace and exits 0
        import traceback
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
