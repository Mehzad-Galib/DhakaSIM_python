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
from .parameters import Parameters


class Constants:
    TYPES_OF_CARS = 13
    PEDESTRIANS_ALONG_THE_ROAD_TYPE = 12
    pedestrian_color = Color.BLACK
    road_border_color = Color.BLACK
    background_color = Color.WHITE  # Color(105, 105, 105) / Color.DARK_GRAY
    DEFAULT_SCALE = 5.0
    TIME_STEP = 1.0
    THRESHOLD_DISTANCE = 0.5
    PRINT_RESULT = True

    MAX_NUMBER_OF_OBJECTS = 2147483647
    MAX_NUMBER_OF_VEHICLES = 2147483647

    TOTAL_NETWORK_ROAD_LENGTH = 3.83

    ROAD_BORDER_COLOR = Color.BLACK
    BACKGROUND_COLOR = Color(240, 240, 240)

    PEDESTRIAN_SIZE = 0.7  # Unit: meter

    # Initialised by initialize(), because they depend on input/parameter.txt.
    p = 0.0
    road_crossing_ped_poisson = None
    road_along_ped_poisson = None

    STANDING_PEDESTRIAN_COLOR = Color(216, 150, 0)
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
    AVG_NUMBER_OF_STANDING_PEDESTRIANS = jint(math.ceil(15.61 * TOTAL_NETWORK_ROAD_LENGTH))

    PARKED_CAR_COLOR = Color(216, 150, 0)
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
    AVG_NUMBER_OF_PARKED_CARS = jint(math.ceil(19.77 * TOTAL_NETWORK_ROAD_LENGTH))

    PARKED_RICKSHAW_COLOR = Color(216, 150, 0)
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
    AVG_NUMBER_OF_PARKED_RICKSHAWS = jint(math.ceil(15.61 * TOTAL_NETWORK_ROAD_LENGTH))

    PARKED_CNG_COLOR = Color(216, 150, 0)
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
    AVG_NUMBER_OF_PARKED_CNGS = jint(math.ceil(4.67 * TOTAL_NETWORK_ROAD_LENGTH))

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
        cls.road_crossing_ped_poisson = PoissonDistribution(
            Parameters.ACROSS_PEDESTRIAN_PER_HOUR / 3600)
        cls.road_along_ped_poisson = PoissonDistribution(
            Parameters.ALONG_PEDESTRIAN_PER_HOUR / 3600)
        cls._initialized = True
