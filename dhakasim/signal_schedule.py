"""Multi-objective traffic signal scheduling.

Implements the Traffic Signal Scheduling Module (TSSM) of

    M. Rahaman, A. M. S. Rumi, M. S. Islam, T. R. Toha, M. M. Mushfiq,
    M. S. Rahman, M. A. Nayeem, N. A. Al-Nabhan and A. B. M. A. Al Islam,
    "Toward Devising A Multi-Objective Traffic Signal Scheduling Approach for
    Non-Lane-Based Heterogeneous Traffic", IEEE Access vol. 13, 2025,
    pp. 172598-172615.

Equation and section numbers in the comments below refer to that paper.

The idea in one paragraph.  At an intersection only one approach can hold a
green at a time, so giving one link more green necessarily makes the others
wait: congestion and delay are in direct conflict and a single weighted score
hides the trade-off.  So the schedule -- one green duration per approach -- is
searched by NSGA-II against *two* objectives, which returns a Pareto front
rather than one answer, and only at the very end is a single schedule picked
off that front (Equations 5 and 6).  Both objectives are written to notice that
the traffic is heterogeneous: a rickshaw clears an intersection at less than
half the rate of a car, so counting the two alike misprices every approach.

Two things are worth knowing before reading on.

**Candidates are scored on a numerical simulator, not on DhakaSim.**  Section
IV-C-2 of the paper is explicit about this and it is not a shortcut: NSGA-II
evaluates tens of thousands of schedules per decision, and each one has to be
scored in microseconds.  :func:`run_cycle` is that simulator -- an isolated
intersection, queues discharging at a fixed rate per vehicle class.  DhakaSim
then plays out the chosen schedule for real, with all its side friction,
non-lane-based movement and interactions between intersections, and *that* is
what the reported speeds and waiting times are measured on.

**The optimiser never touches ``Parameters.random``.**  That stream is what
keeps a seeded run bit-identical to the Java original, and drawing from it here
would shift every vehicle generated afterwards.  This module carries its own
:class:`random.Random`, seeded from the run's seed so results stay
reproducible.  See :func:`seed_from`.
"""

from __future__ import annotations

import math
import random

# --------------------------------------------------------------------------
# vehicle classes
# --------------------------------------------------------------------------

#: Vehicle type indices below this are human-powered: 0 bicycle, 1 rickshaw,
#: 2 van/cart.  Everything from 3 up is motorised, and 12 is a pedestrian
#: rather than a vehicle.  This is the same split ``Processor`` already applies
#: to the per-class statistics, and the two must agree or the optimiser will be
#: tuning for one definition while the report measures another.
FIRST_MOTORISED_TYPE = 3
PEDESTRIAN_TYPE = 12


def is_motorised(vehicle_type: int) -> bool:
    return vehicle_type >= FIRST_MOTORISED_TYPE and vehicle_type != PEDESTRIAN_TYPE


# --------------------------------------------------------------------------
# the numerical simulator (Section IV-C-2)
# --------------------------------------------------------------------------

#: Vehicles per second of green that each class clears the stop line at.  The
#: paper's figures.  The gap between them is the whole reason a heterogeneous
#: objective is worth having: an approach queued with rickshaws needs more than
#: twice the green of the same queue of cars.
MOTORISED_FLOW = 0.9
NON_MOTORISED_FLOW = 0.4


class Demand:
    """How many vehicles of each class are waiting on one approach.

    Counts are floats rather than ints because the numerical simulator carries
    a fractional remainder from one cycle to the next; rounding each cycle
    would quietly discard up to half a vehicle per approach per cycle, which
    over a long run is most of an approach.
    """

    __slots__ = ("motorised", "non_motorised")

    def __init__(self, motorised: float = 0.0, non_motorised: float = 0.0):
        self.motorised = float(motorised)
        self.non_motorised = float(non_motorised)

    @property
    def total(self) -> float:
        return self.motorised + self.non_motorised

    def __repr__(self) -> str:                                # pragma: no cover
        return f"Demand(motorised={self.motorised}, non_motorised={self.non_motorised})"


def run_cycle(demands, greens):
    """Play one signal cycle out on an isolated intersection.

    Returns ``(remaining, reds)``: what is left queued on each approach once
    the cycle has finished, and how long each approach spent on red.

    The two classes discharge *concurrently*, each at its own rate, for as long
    as the approach holds its green.  That is the reading the non-lane-based
    setting demands -- a rickshaw and a car released together do not queue
    behind one another, they leave side by side -- and it is what makes the
    weighted objective in Equation 3 mean anything.  The paper states the two
    flow rates and leaves the rest to the reader; this is the assumption.

    The red duration of an approach is the rest of the cycle, since exactly one
    approach is green at any moment.  That is the paper's own approximation of
    waiting time (Equation 2).
    """
    cycle = sum(greens)
    remaining = []
    reds = []
    for demand, green in zip(demands, greens):
        left = Demand(
            max(0.0, demand.motorised - MOTORISED_FLOW * green),
            max(0.0, demand.non_motorised - NON_MOTORISED_FLOW * green))
        remaining.append(left)
        reds.append(cycle - green)
    return remaining, reds


# --------------------------------------------------------------------------
# the objective functions (Section IV-C-1)
# --------------------------------------------------------------------------

def objectives_v1(demands, greens):
    """Equations 1 and 2: congestion and delay, blind to vehicle class.

    The baseline the paper introduces in order to beat it.  Every vehicle
    counts once towards congestion, and every second of red counts once
    towards delay however many vehicles are sitting through it.
    """
    remaining, reds = run_cycle(demands, greens)
    f1 = sum(left.total for left in remaining)
    f2 = sum(reds)
    return f1, f2


def objectives_v2(demands, greens, motorised_weight):
    """Equations 3 and 4: the same two quantities, priced by vehicle class.

    Equation 3 weights the two classes against each other with *w*, so that a
    queue of rickshaws and a queue of cars do not contribute equally to
    congestion.  Equation 4 turns total red time into red time *per vehicle
    waiting through it*, which is what a delay actually costs: sixty seconds of
    red on an empty approach and sixty on a full one are not the same event.

    ``C(i)`` in Equation 4 is the number of vehicles that arrived on the
    approach, not the number left when the cycle ends.  The paper can be read
    either way -- it defines ``C(i)`` as "the total number of vehicles, both
    motorized and non-motorized, on link i", which is this reading, but calls
    the product "the red signal duration of each link with the number of
    vehicles waiting on that link", and its ``C_m``/``C_nm`` are counts
    *remaining* after the cycle.  Both were implemented and measured over the
    four Dhaka networks; the arriving count is better on all four, and the
    remaining count turns out to be degenerate -- a schedule that clears every
    approach scores zero on both objectives, so the entire population ties and
    the vehicle-class weight stops having any effect at all.  See README.
    """
    remaining, reds = run_cycle(demands, greens)
    f1 = sum(motorised_weight * left.motorised
             + (1.0 - motorised_weight) * left.non_motorised
             for left in remaining)
    waiting = sum(demand.total * red for demand, red in zip(demands, reds))
    queued = sum(demand.total for demand in demands)
    # An intersection with nothing on it has no average delay to speak of, and
    # the alternative is a division by zero on the quietest hour of the day.
    f2 = waiting / queued if queued > 0 else 0.0
    return f1, f2


# --------------------------------------------------------------------------
# NSGA-II, real-encoded (Section IV-C-3)
# --------------------------------------------------------------------------

#: Population size, from the paper.
POPULATION = 50

#: Distribution index for both SBX crossover and polynomial mutation.  20 is
#: jMetal's default and the paper's choice: large enough that a child lands
#: near its parents, so the search exploits what it has found, small enough to
#: still explore.
DISTRIBUTION_INDEX = 20.0


class _Individual:
    """One candidate schedule and everything the sort needs to rank it."""

    __slots__ = ("genes", "f1", "f2", "rank", "crowding")

    def __init__(self, genes):
        self.genes = genes
        self.f1 = 0.0
        self.f2 = 0.0
        self.rank = 0
        self.crowding = 0.0

    def dominates(self, other) -> bool:
        """Pareto dominance for a pure minimisation problem."""
        return ((self.f1 <= other.f1 and self.f2 <= other.f2)
                and (self.f1 < other.f1 or self.f2 < other.f2))


def _non_dominated_sort(population):
    """Deb's fast non-dominated sort: split the population into fronts.

    O(MN^2) and, as the paper notes, the most expensive part of a generation.
    """
    fronts = [[]]
    dominated_by = [[] for _ in population]
    domination_count = [0] * len(population)
    for i, p in enumerate(population):
        for j, q in enumerate(population):
            if i == j:
                continue
            if p.dominates(q):
                dominated_by[i].append(j)
            elif q.dominates(p):
                domination_count[i] += 1
        if domination_count[i] == 0:
            p.rank = 0
            fronts[0].append(i)
    current = 0
    while fronts[current]:
        following = []
        for i in fronts[current]:
            for j in dominated_by[i]:
                domination_count[j] -= 1
                if domination_count[j] == 0:
                    population[j].rank = current + 1
                    following.append(j)
        current += 1
        fronts.append(following)
    return [[population[i] for i in front] for front in fronts if front]


def _crowding_distance(front):
    """Spread within one front, so the selection keeps the ends of it.

    Without this the population collapses onto whichever part of the front the
    search found first, and the whole point of running a multi-objective
    algorithm -- being handed the trade-off rather than one point on it -- is
    lost.
    """
    for individual in front:
        individual.crowding = 0.0
    if len(front) <= 2:
        for individual in front:
            individual.crowding = math.inf
        return
    for key in ("f1", "f2"):
        front.sort(key=lambda individual: getattr(individual, key))
        low = getattr(front[0], key)
        high = getattr(front[-1], key)
        front[0].crowding = math.inf
        front[-1].crowding = math.inf
        span = high - low
        if span <= 0.0:
            continue                       # every solution ties on this axis
        for i in range(1, len(front) - 1):
            front[i].crowding += (getattr(front[i + 1], key)
                                  - getattr(front[i - 1], key)) / span


def _tournament(population, rng):
    """Binary tournament on (rank, crowding) -- the paper's selection operator."""
    a = population[rng.randrange(len(population))]
    b = population[rng.randrange(len(population))]
    if a.rank != b.rank:
        return a if a.rank < b.rank else b
    return a if a.crowding > b.crowding else b


def _sbx(parent_a, parent_b, low, high, probability, rng):
    """Simulated binary crossover, one gene at a time.

    *probability* is per gene and the paper sets it to the inverse of the
    number of links, so on average a single approach's green time is swapped
    per offspring rather than the whole schedule being mixed.  A schedule is a
    set of durations that only makes sense together, and mixing all of them at
    once destroys more good structure than it finds.
    """
    child_a = list(parent_a)
    child_b = list(parent_b)
    for i in range(len(child_a)):
        if rng.random() > probability:
            continue
        x1, x2 = child_a[i], child_b[i]
        if abs(x1 - x2) < 1e-12:
            continue
        if x1 > x2:
            x1, x2 = x2, x1
        u = rng.random()
        if u <= 0.5:
            beta = (2.0 * u) ** (1.0 / (DISTRIBUTION_INDEX + 1.0))
        else:
            beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (DISTRIBUTION_INDEX + 1.0))
        middle = 0.5 * (x1 + x2)
        half = 0.5 * beta * (x2 - x1)
        child_a[i] = min(max(middle - half, low), high)
        child_b[i] = min(max(middle + half, low), high)
    return child_a, child_b


def _polynomial_mutation(genes, low, high, probability, rng):
    """Polynomial mutation, again one gene at a time at 1/L."""
    span = high - low
    if span <= 0.0:
        return genes
    for i in range(len(genes)):
        if rng.random() > probability:
            continue
        x = genes[i]
        left = (x - low) / span
        right = (high - x) / span
        u = rng.random()
        power = 1.0 / (DISTRIBUTION_INDEX + 1.0)
        if u <= 0.5:
            delta = (2.0 * u + (1.0 - 2.0 * u)
                     * (1.0 - left) ** (DISTRIBUTION_INDEX + 1.0)) ** power - 1.0
        else:
            delta = 1.0 - (2.0 * (1.0 - u) + 2.0 * (u - 0.5)
                           * (1.0 - right) ** (DISTRIBUTION_INDEX + 1.0)) ** power
        genes[i] = min(max(x + delta * span, low), high)
    return genes


def _pick_from_front(front, objective_weight):
    """Collapse a Pareto front to the one schedule to actually run.

    Equation 5 normalises each objective across the front, which is what stops
    whichever objective happens to carry the larger numbers from deciding the
    answer on its own -- congestion is a count of vehicles and delay is a count
    of seconds, and at a busy intersection the second is two orders of
    magnitude larger.  Equation 6 then takes the weighted sum and the smallest
    wins.
    """
    lowest_1 = min(individual.f1 for individual in front)
    highest_1 = max(individual.f1 for individual in front)
    lowest_2 = min(individual.f2 for individual in front)
    highest_2 = max(individual.f2 for individual in front)
    span_1 = highest_1 - lowest_1
    span_2 = highest_2 - lowest_2

    best = None
    best_fitness = math.inf
    best_cycle = math.inf
    for individual in front:
        # Equation 5: a front that ties on an objective normalises to zero
        # there, rather than dividing by nothing.
        n1 = (individual.f1 - lowest_1) / span_1 if span_1 != 0 else 0.0
        n2 = (individual.f2 - lowest_2) / span_2 if span_2 != 0 else 0.0
        fitness = objective_weight * n1 + (1.0 - objective_weight) * n2
        cycle = sum(individual.genes)
        # The shorter cycle breaks a tie.  Equation 6 alone does not say what
        # to do when two schedules score the same, and on an intersection with
        # nothing waiting on it *every* schedule scores the same -- both
        # objectives are zero -- so without this the answer is whichever
        # random individual happened to come first, which at the start of a
        # run is how an empty junction ends up holding one approach green for
        # ten minutes.  A shorter cycle gives every approach its turn sooner
        # and can never be worse on either objective, so this only ever agrees
        # with them.
        if fitness < best_fitness or (fitness == best_fitness
                                      and cycle < best_cycle):
            best_fitness = fitness
            best_cycle = cycle
            best = individual
    return best


def optimise(demands, *, version=2, motorised_weight=0.2, objective_weight=0.5,
             green_min=5.0, green_max=600.0, population=POPULATION,
             evaluations=2000, rng=None):
    """Search for a signal schedule with NSGA-II.  Returns green times, seconds.

    One gene per approach, real-encoded.  The paper compared this against a
    10-bit binary encoding and real encoding won on both objectives across the
    whole front (its Figure 7), which is why there is only one encoding here.
    """
    rng = rng or random.Random(0)
    links = len(demands)
    if links == 0:
        return []
    if links == 1:
        # One approach is not a scheduling problem; give it everything.
        return [green_max]

    def score(individual):
        if version == 1:
            individual.f1, individual.f2 = objectives_v1(demands, individual.genes)
        else:
            individual.f1, individual.f2 = objectives_v2(
                demands, individual.genes, motorised_weight)

    # The paper's inverse-scaling rule: on average one approach is touched per
    # offspring, by each operator.
    per_gene = 1.0 / links

    parents = []
    for _ in range(population):
        individual = _Individual([rng.uniform(green_min, green_max)
                                  for _ in range(links)])
        score(individual)
        parents.append(individual)
    spent = population

    fronts = _non_dominated_sort(parents)
    for front in fronts:
        _crowding_distance(front)

    while spent < evaluations:
        offspring = []
        while len(offspring) < population:
            mother = _tournament(parents, rng)
            father = _tournament(parents, rng)
            first, second = _sbx(mother.genes, father.genes,
                                 green_min, green_max, per_gene, rng)
            for genes in (first, second):
                if len(offspring) >= population:
                    break
                child = _Individual(_polynomial_mutation(
                    genes, green_min, green_max, per_gene, rng))
                score(child)
                offspring.append(child)
        spent += len(offspring)

        # Elitist replacement: parents and offspring compete together, so a
        # good schedule is never lost to an unlucky generation.
        combined = parents + offspring
        fronts = _non_dominated_sort(combined)
        parents = []
        for front in fronts:
            _crowding_distance(front)
            if len(parents) + len(front) <= population:
                parents.extend(front)
            else:
                front.sort(key=lambda individual: -individual.crowding)
                parents.extend(front[:population - len(parents)])
                break

    best = _pick_from_front(_non_dominated_sort(parents)[0], objective_weight)
    return _trim_empty_approaches(list(best.genes), demands, green_min)


def _trim_empty_approaches(greens, demands, green_min):
    """An approach with nothing waiting on it gets the minimum green.

    A repair step rather than part of the objectives, because it is not a
    preference -- it is arithmetic.  An empty approach discharges nothing
    however long it is held, so every second of green it is given is a second
    of red for every other approach and buys nothing at all.  Both objectives
    already want this; they simply cannot express it when the whole
    intersection is empty and every candidate scores zero.

    The paper's numerical simulator starts every link with a fixed number of
    vehicles on it, so the empty case never arises there.  In a network
    simulation it arises constantly: at the start of a run, on a quiet
    approach, and on any one-way arm that only ever takes traffic away.
    """
    return [green_min if demand.total <= 0 else green
            for green, demand in zip(greens, demands)]


# --------------------------------------------------------------------------
# the baselines (Sections IV-A and IV-B)
# --------------------------------------------------------------------------

def fixed_schedule(links, green):
    """The same green for every approach, cycle after cycle.

    What most of Dhaka's few working signals do, and the baseline every
    improvement in the paper is measured against.
    """
    return [float(green)] * links


def biased_random_schedule(demands, *, low=5.0, high=600.0, green_min=5.0,
                           green_max=600.0, rng=None):
    """A random green scaled by each approach's share of the traffic.

    Not a real control strategy and the paper does not claim it is one.  It is
    in the study because it is the closest thing to what actually happens at
    most Dhaka intersections: a traffic policeman looking at the queues and
    holding a hand up for about as long as they seem to deserve.
    """
    rng = rng or random.Random(0)
    total = sum(demand.total for demand in demands)
    greens = []
    for demand in demands:
        share = (demand.total / total) if total > 0 else 1.0 / max(len(demands), 1)
        greens.append(min(max(rng.uniform(low, high) * share, green_min),
                          green_max))
    return greens


# --------------------------------------------------------------------------
# the module the simulator talks to (Algorithm 1)
# --------------------------------------------------------------------------

#: Every mode ``Parameters.SIGNAL_MODE`` accepts.
MODES = ("fixed", "biased-random", "moo-v1", "moo-v2")


def seed_from(seed):
    """A private RNG for the optimiser, reproducible but off the hot stream.

    ``Parameters.random`` decides what vehicles get generated and when, and it
    is what a seeded run's bit-for-bit agreement with the Java original rests
    on.  Taking even one draw from it here would move every vehicle after it.
    """
    return random.Random(0 if seed is None or seed < 0 else seed)


def schedule(demands, mode, *, rng, fixed_green=15.0, motorised_weight=0.2,
             objective_weight=0.5, green_min=5.0, green_max=600.0,
             population=POPULATION, evaluations=2000,
             random_low=5.0, random_high=600.0):
    """One decision of the Traffic Signal Scheduling Module.

    *demands* is one :class:`Demand` per approach, in the order the
    intersection cycles through them; the returned green times are in seconds
    and in the same order.
    """
    links = len(demands)
    if links == 0:
        return []
    if mode == "fixed":
        return fixed_schedule(links, fixed_green)
    if mode == "biased-random":
        return biased_random_schedule(
            demands, low=random_low, high=random_high,
            green_min=green_min, green_max=green_max, rng=rng)
    if mode in ("moo-v1", "moo-v2"):
        return optimise(demands, version=1 if mode == "moo-v1" else 2,
                        motorised_weight=motorised_weight,
                        objective_weight=objective_weight,
                        green_min=green_min, green_max=green_max,
                        population=population, evaluations=evaluations, rng=rng)
    raise ValueError(f"unknown signal mode {mode!r}; expected one of {MODES}")
