"""Port of the ``thesisfinal.Constants`` interface.

The Java interface holds two kinds of field.  Compile-time constants are plain
class attributes here.  The three fields that depend on ``Parameters`` --
``p`` and the two Poisson distributions -- are only initialised when the
interface is first *actively* used, which in the Java build happens inside
``Processor`` and therefore after ``Utilities.initialize()`` has read
``input/parameter.txt``.  :meth:`Constants.initialize` reproduces that timing
and is called from :mod:`dhakasim.dhaka_sim` right after the parameters load.
"""

from __future__ import annotations

import math

from .javacompat import Color, PoissonDistribution, jint
from .normal_distribution import NormalDistribution
from .parameters import Parameters, scratch_random


class Constants:
    TYPES_OF_CARS = 13
    PEDESTRIANS_ALONG_THE_ROAD_TYPE = 12

    # Circulating speed in a roundabout is taken as k*sqrt(radius) m/s, the
    # usual side-friction form for a curve. k = 1.7 puts a 16 m island at
    # ~24 km/h and a 29 m one at ~33 km/h, which matches observed practice.
    ROUNDABOUT_SPEED_FACTOR = 1.7
    # Width of the circulatory carriageway, in metres: the ring of road
    # between the island and the outer kerb.  Two circulating lanes, which is
    # what both surveyed circles have.  A `roundabout` line in geometry.txt may
    # state its own width as a third value.  It is not the width of the widest
    # entry: an approach arriving on seven lanes still circulates on two.
    ROUNDABOUT_CIRCULATORY_WIDTH = 7.0

    # Simulation steps a driver will wait in the wrong lane before forcing the
    # turn anyway. Prevents a mis-positioned vehicle deadlocking its approach,
    # since lane changing here is not destination-aware.
    TURN_LANE_PATIENCE = 20

    # Fixed display colour per vehicle type, so the animation and the report
    # legend agree. Indexes match the 13 vehicle types (see report.TYPE_NAMES):
    # cars (4-6) share a red family, buses (8-9) a purple family, trucks
    # (10-11) a grey family; CNG is Dhaka-green.
    VEHICLE_TYPE_COLORS = (
        (0, 160, 160),    # 0  bicycle      - teal
        (224, 102, 12),   # 1  rickshaw     - deep orange
        (140, 90, 40),    # 2  van / cart   - brown
        (40, 110, 220),   # 3  motorbike    - blue
        (210, 50, 50),    # 4  car          - red
        (170, 30, 60),    # 5  car          - crimson
        (240, 105, 105),  # 6  car          - light red
        (30, 160, 70),    # 7  CNG / auto   - green
        (140, 60, 190),   # 8  bus          - purple
        (200, 70, 170),   # 9  bus          - magenta
        (100, 100, 100),  # 10 truck        - grey
        (55, 55, 55),     # 11 truck        - dark grey
        (0, 0, 0),        # 12 pedestrian   - black
    )
    pedestrian_color = Color.BLACK
    # Dark slate rather than pure black: the edge has to hold its own against
    # a white background without drowning the painted lane dividers.
    road_border_color = Color(58, 65, 73)
    # Filled road surface; junctions are filled with the same colour so that
    # intersections read as one smooth area instead of a tangle of kerb stubs.
    # White, like an engineering plan: the kerb draws the road and the fill is
    # there to cover what lies under it, not to colour it in.  A whisker off
    # pure white and no more -- the background is white too, and at report
    # scale the kerb is a sub-pixel line, so a carriageway with no tint at all
    # would leave the road with nothing to be seen by.  This is also the
    # colour the 3D view lays on its pale ground.  The markings below are grey
    # for the same reason.
    road_fill_color = Color(246, 247, 249)
    # central island of a roundabout (planted, so a muted green)
    island_fill_color = Color(198, 214, 190)
    #: Painted lane dividers.  Grey, because the carriageway they are painted
    #: on is white: real road markings are pale against dark asphalt, but a
    #: pale marking on a white road is not there at all.
    lane_marking_color = Color(152, 160, 170)
    #: Drawn over map imagery instead of the three colours above.  Aerial
    #: photography is dark, busy and every colour at once, so the kerb is a
    #: near-black slate that holds against all of it, and the markings a mid
    #: grey that reads on the white wash without competing with the kerb.  An
    #: earlier version used indigo, which was chosen against street-map
    #: rendering; over photography it only tinted the road blue.
    overlay_border_color = Color(30, 38, 48)
    overlay_marking_color = Color(122, 132, 145)
    #: Carriageway drawn over imagery: white, and painted solid.
    overlay_fill_color = Color(255, 255, 255)
    #: How solid that paint is.  This used to be a wash at just under a half,
    #: on the argument that a reader wants to see the photograph under the
    #: road.  They do not: what a wash actually gives is a dithered grey that
    #: reads as neither road nor imagery, and every place the arms, the
    #: junction patch and the connectors overlap comes out a different shade,
    #: so the junction looks blotched exactly where it matters most.  Solid,
    #: the road is a road and the imagery is context around it.  The stipple
    #: machinery stays -- ``set_alpha`` is part of the drawing-surface
    #: protocol and the islands and the 3D view still use it -- but at 1.0 the
    #: canvas takes the fast path and never dithers.
    OVERLAY_FILL_ALPHA = 1.0

    #: Metres added to every carriageway *when drawing over map imagery*, and
    #: nowhere else.  A rendered road width is a cartographic choice: OSM draws
    #: a trunk road about eleven metres wide whatever it measures, and a wide
    #: street gets no wider than a narrow one.  A survey carriageway laid over
    #: that leaves the casing sticking out along both kerbs, which reads as the
    #: model being in the wrong place when it is not.  Six metres covers the
    #: casing on the classes the Dhaka networks use without swallowing the
    #: footpath.  The simulation never sees it -- strips, capacity and every
    #: statistic come from the real width in ``link.txt``.  Set to 0.0 to draw
    #: the surveyed width exactly.
    OVERLAY_WIDEN_METRES = 6.0
    background_color = Color.WHITE  # Color(105, 105, 105) / Color.DARK_GRAY
    DEFAULT_SCALE = 5.0
    TIME_STEP = 1.0
    THRESHOLD_DISTANCE = 0.5
    PRINT_RESULT = True

    MAX_NUMBER_OF_OBJECTS = 2147483647
    MAX_NUMBER_OF_VEHICLES = 2147483647

    # Roadside-object density per kilometre of road, from the field study
    # behind the simulator.  The absolute counts below are these rates times
    # the length of the network actually loaded -- see calibrate_to_network().
    STANDING_PEDESTRIANS_PER_KM = 15.61
    PARKED_CARS_PER_KM = 19.77
    PARKED_RICKSHAWS_PER_KM = 15.61
    PARKED_CNGS_PER_KM = 4.67

    # Replaced by the measured length once a network is read.  The value here
    # is only what the counts start at beforehand, and is what the Java
    # original used for every network regardless of size.
    TOTAL_NETWORK_ROAD_LENGTH = 1.01

    ROAD_BORDER_COLOR = Color.BLACK
    BACKGROUND_COLOR = Color(240, 240, 240)

    PEDESTRIAN_SIZE = 0.7  # Unit: meter

    # Initialised by initialize(), because they depend on input/parameter.txt.
    p = 0.0
    road_crossing_ped_poisson = None
    road_along_ped_poisson = None

    # --- side friction ----------------------------------------------------
    # Parked vehicles and standing pedestrians are obstructions, not traffic.
    # They used to share one hazard-yellow family, differing by lightness
    # only, and the owner could not tell the four apart in the legend or on
    # the road (28 Aug).  Each type now gets its own hue, chosen pale and
    # unsaturated so the group still reads as "not traffic" beside the
    # saturated moving palette, while no two members share a hue.  None of
    # these may drift near a VEHICLE_TYPE_COLORS entry: the nearest
    # neighbours are noted per line.
    STANDING_PEDESTRIAN_COLOR = Color(255, 213, 20)   # vivid yellow -- nothing moving is yellow
    STANDING_PEDESTRIAN_LENGTH = PEDESTRIAN_SIZE  # Unit: meter
    STANDING_PEDESTRIAN_WIDTH = PEDESTRIAN_SIZE  # Unit: meter
    STANDING_PEDESTRIAN_TIME_LIMIT_FACTOR = 50
    STANDING_PEDESTRIAN_MIN_BLOCKAGE = 0.21  # Unit: meter
    STANDING_PEDESTRIAN_MAX_BLOCKAGE = 6.00  # Unit: meter
    STANDING_PEDESTRIAN_DISTRIBUTIONS = (
        NormalDistribution(0.4635, 0.825, 0.40),
        NormalDistribution(0.3250, 1.925, 0.45),
        NormalDistribution(0.1350, 2.900, 0.42),
        NormalDistribution(0.0640, 3.710, 0.35),
        NormalDistribution(0.0070, 5.750, 0.40),
    )
    AVG_NUMBER_OF_STANDING_PEDESTRIANS = jint(math.ceil(
        STANDING_PEDESTRIANS_PER_KM * TOTAL_NETWORK_ROAD_LENGTH))

    PARKED_CAR_COLOR = Color(105, 185, 235)           # sky blue -- far lighter than the motorbike's (40,110,220)
    PARKED_CAR_LENGTH = 4.5  # Unit: meter
    PARKED_CAR_WIDTH = 1.7  # Unit: meter
    PARKED_CAR_TIME_LIMIT_FACTOR = STANDING_PEDESTRIAN_TIME_LIMIT_FACTOR * 10
    PARKED_CAR_MIN_BLOCKAGE = 0.70  # Unit: meter
    PARKED_CAR_MAX_BLOCKAGE = 6.00  # Unit: meter
    PARKED_CAR_DISTRIBUTIONS = (
        NormalDistribution(0.050, 1.12, 0.390),
        NormalDistribution(0.515, 2.07, 0.322),
        NormalDistribution(0.200, 2.73, 0.310),
        NormalDistribution(0.172, 3.64, 0.334),
        NormalDistribution(0.042, 4.48, 0.300),
        NormalDistribution(0.010, 5.92, 0.310),
    )
    AVG_NUMBER_OF_PARKED_CARS = jint(math.ceil(
        PARKED_CARS_PER_KM * TOTAL_NETWORK_ROAD_LENGTH))

    PARKED_RICKSHAW_COLOR = Color(198, 150, 228)      # lavender -- far lighter than the bus's (140,60,190)
    PARKED_RICKSHAW_LENGTH = 3.0  # Unit: meter
    PARKED_RICKSHAW_WIDTH = 1.0  # Unit: meter
    PARKED_RICKSHAW_TIME_LIMIT_FACTOR = PARKED_CAR_TIME_LIMIT_FACTOR // 5
    PARKED_RICKSHAW_MIN_BLOCKAGE = 0.56  # Unit: meter
    PARKED_RICKSHAW_MAX_BLOCKAGE = 6.00  # Unit: meter
    PARKED_RICKSHAW_DISTRIBUTIONS = (
        NormalDistribution(0.582, 1.57, 0.500),
        NormalDistribution(0.258, 2.76, 0.440),
        NormalDistribution(0.085, 3.67, 0.320),
        NormalDistribution(0.046, 4.65, 0.380),
        NormalDistribution(0.009, 5.88, 0.380),
    )
    AVG_NUMBER_OF_PARKED_RICKSHAWS = jint(math.ceil(
        PARKED_RICKSHAWS_PER_KM * TOTAL_NETWORK_ROAD_LENGTH))

    PARKED_CNG_COLOR = Color(150, 214, 150)           # pale green -- its moving hue washed out, far lighter than the CNG's (30,160,70)
    PARKED_CNG_LENGTH = 2.6  # Unit: meter
    PARKED_CNG_WIDTH = 1.3  # Unit: meter
    PARKED_CNG_TIME_LIMIT_FACTOR = PARKED_CAR_TIME_LIMIT_FACTOR // 5
    PARKED_CNG_MIN_BLOCKAGE = 0.95  # Unit: meter
    PARKED_CNG_MAX_BLOCKAGE = 4.67  # Unit: meter
    PARKED_CNG_DISTRIBUTIONS = (
        NormalDistribution(0.119, 1.13, 0.29),
        NormalDistribution(0.730, 2.30, 0.55),
        NormalDistribution(0.065, 3.81, 0.27),
        NormalDistribution(0.015, 4.65, 0.32),
    )
    AVG_NUMBER_OF_PARKED_CNGS = jint(math.ceil(
        PARKED_CNGS_PER_KM * TOTAL_NETWORK_ROAD_LENGTH))

    WALKING_PEDESTRIAN_MIN_BLOCKAGE = 0.50  # Unit: meter
    WALKING_PEDESTRIAN_MAX_BLOCKAGE = 7.75  # Unit: meter
    WALKING_PEDESTRIAN_DISTRIBUTIONS = (
        NormalDistribution(0.529, 1.60, 0.531),
        NormalDistribution(0.471, 3.38, 1.388),
    )

    _initialized = False

    @classmethod
    def initialize(cls) -> None:
        """Build the parameter-dependent fields.

        Mirrors Java interface initialisation, which happens lazily after
        ``input/parameter.txt`` has been read.
        """
        if cls._initialized:
            return
        cls.p = (Parameters.ACROSS_PEDESTRIAN_PER_HOUR * 1.0) / 3600
        # PoissonDistribution defaults to its own unseeded generator, matching
        # commons-math; hand it the shared one so pedestrian arrivals follow
        # the seed too (see parameters.scratch_random).
        cls.road_crossing_ped_poisson = PoissonDistribution(
            Parameters.ACROSS_PEDESTRIAN_PER_HOUR / 3600, scratch_random())
        cls.road_along_ped_poisson = PoissonDistribution(
            Parameters.ALONG_PEDESTRIAN_PER_HOUR / 3600, scratch_random())
        cls._initialized = True

    @classmethod
    def calibrate_to_network(cls, total_road_length_km: float) -> None:
        """Scale the roadside-object targets to the network that was loaded.

        The counts are a density -- so many parked cars per kilometre, from the
        field study -- but they were baked in against one fixed road length, so
        every network got the same absolute number of objects however large it
        was.  Unlike :meth:`initialize` this runs per simulation, since the GUI
        can load a different network without restarting.
        """
        cls.TOTAL_NETWORK_ROAD_LENGTH = total_road_length_km
        cls.AVG_NUMBER_OF_STANDING_PEDESTRIANS = jint(math.ceil(
            cls.STANDING_PEDESTRIANS_PER_KM * total_road_length_km))
        cls.AVG_NUMBER_OF_PARKED_CARS = jint(math.ceil(
            cls.PARKED_CARS_PER_KM * total_road_length_km))
        cls.AVG_NUMBER_OF_PARKED_RICKSHAWS = jint(math.ceil(
            cls.PARKED_RICKSHAWS_PER_KM * total_road_length_km))
        cls.AVG_NUMBER_OF_PARKED_CNGS = jint(math.ceil(
            cls.PARKED_CNGS_PER_KM * total_road_length_km))
