"""Port of ``thesisfinal.Parameters``.

Simulation-wide mutable configuration, read from ``input/parameter.txt`` by
:func:`dhakasim.utilities.initialize`.  Java's static fields become class
attributes so every module sees the same values, exactly as before.
"""

from __future__ import annotations

from enum import Enum

from .javacompat import JavaRandom


class DLC_MODEL(Enum):
    """Discretionary lane changing models."""

    NAIVE_MODEL = 0  # no model at all; just instantly changes lane if possible
    GIPPS_MODEL = 1
    GHR_MODEL = 2
    MOBIL_MODEL = 3


class CAR_FOLLOWING_MODEL(Enum):
    NAIVE_MODEL = 0   # no model at all; moves as fast as possible, brakes as hard as possible
    GIPPS_MODEL = 1   # moving velocity is controlled; but no braking; so collision could not be avoided
    HYBRID_MODEL = 2  # velocity from GIPPS model, braking hard in emergency
    KRAUSS_MODEL = 3
    GFM_MODEL = 4
    IDM_MODEL = 5
    RVF_MODEL = 6
    VFIAC_MODEL = 7
    OVCM_MODEL = 8
    KFTM_MODEL = 9
    HDM_MODEL = 10
    SBM_MODEL = 11
    MY_MODEL = 12
    MODIFIED_NEWTONIAN_MODEL = 13


class VEHICLE_GENERATION_RATE(Enum):
    POISSON = 0
    CONSTANT = 1


class Parameters:
    # Optional map of node id -> human-readable name (loaded from
    # input/node_names.txt if present); used for GUI labels and the report.
    NODE_NAMES = {}
    # Number of frames captured for the report's embedded animation (0 = off).
    REPORT_ANIMATION_FRAMES = 24
    # Open the GUI in the 3D perspective view rather than the 2D plan view.
    # Purely a matter of how the animation is drawn -- the simulation itself is
    # identical either way, and the view can be switched at any time while a
    # run is going.
    RENDER_3D = False
    # Sub-folder of input/ holding the selected network's files ("" = input/).
    NETWORK_DIR = ""
    # Hour of the survey day to simulate, 0-23. -1 = the network's peak hour
    # (the default demand.txt / vehicle_mix.txt).
    TIME_OF_DAY = -1
    # Real-world geometry extensions (physical medians, and later one-way links,
    # roundabout circulation and turn-lane channelisation). OFF by default: with
    # GEOMETRY_MODE False the simulator stays byte-identical to the Java
    # reference. Read from the selected network's geometry.txt.
    GEOMETRY_MODE = False
    # link id -> median width in metres
    MEDIAN_WIDTHS = {}
    # node id -> roundabout radius in metres
    ROUNDABOUTS = {}
    # link ids that carry traffic in one direction only, so the whole
    # carriageway serves that direction instead of half being reserved
    ONEWAY_LINKS = set()
    # (fromLinkId, toLinkId) -> (firstStrip, lastStrip): the band of strips a
    # movement must be in to take that turn (turn pockets / lane arrows)
    TURN_LANES = {}
    # Survey vehicle mix: list of (cumulative_threshold_per_10000, type_index).
    # Empty means fall back to the built-in distribution.
    VEHICLE_MIX = []
    # The Java original ends parameter parsing by overwriting the slow/medium/
    # fast split with 25/25/50, whatever the file said, so those three settings
    # have never actually done anything.  True keeps that (and with it byte
    # parity); False lets SlowVehicle/MediumVehicle/FastVehicle through, which
    # is what a study varying the speed-class mix needs.
    VEHICLE_MIX_OVERRIDE = True
    # Set by --seed. See scratch_random() below for what it changes.
    DETERMINISTIC = False
    # Road length in km used to size the roadside-object population. Negative
    # (the default) measures it from the loaded network; a value pins it, which
    # is what reproducing the Java original needs -- it always used 1.01.
    NETWORK_ROAD_LENGTH = -1.0
    # Root for this run's outputs. Every CSV is appended to, never truncated,
    # so a batch of runs sharing one directory produces files whose rows cannot
    # be told apart; the experiment harness gives each run its own.
    STATS_DIR = "statistics"
    # Vehicles/hour for every OD pair, overriding demand.txt. Negative uses the
    # file. A study that varies demand wants one rate across the network, which
    # is otherwise only reachable by rewriting demand.txt between runs.
    DEMAND_OVERRIDE = -1.0
    # Added to every demand row. The Java original hardcodes 30, which is
    # nothing on an 8-row junction and +3300 veh/h across demo_backup's 110
    # rows, so a demand sweep usually wants this at 0.
    DEMAND_OFFSET = 30
    simulation_step = 1
    simulation_end_time = 0
    pixel_per_strip = 0.0
    pixel_per_meter = 0.0
    pixel_per_footpath_strip = 0.0
    simulation_speed = 0
    encounter_per_accident = 0.0  # ----------------------------------- rename and redefine
    strip_width = 0.0
    footpath_strip_width = 0.0
    maximum_speed = 0.0
    across_pedestrian_mode = False
    along_pedestrian_mode = False
    DEBUG_MODE = False
    TRACE_MODE = False
    random = None
    seed = 0
    SIGNAL_CHANGE_DURATION = 0
    DEFAULT_TRANSLATE_X = 0.0
    DEFAULT_TRANSLATE_Y = 0.0
    CENTERED_VIEW = False
    slow_vehicle_percentage = 0.0
    medium_vehicle_percentage = 0.0
    fast_vehicle_percentage = 0.0
    TTC_THRESHOLD = 0.0
    lane_changing_model = None
    car_following_model = None
    vehicle_generation_rate = None
    ERROR_MODE = False
    OBJECT_MODE = False
    FT_METHOD = 0
    NO_OF_READINGS = 0
    M_FACTOR = 0.0
    GUI_MODE = False
    ALPHA = 0.0
    BETA = 0.0
    ETA = 0.0
    ACROSS_PEDESTRIAN_LIMIT = 0  # probability of generating pedestrian is 1/this parameter
    ACROSS_PEDESTRIAN_PER_HOUR = 0.0
    ALONG_PEDESTRIAN_PER_HOUR = 0.0
    ALONG_PEDESTRIAN_PERCENTAGE = 0
    NO_OF_ROUTES_FOR_STAT = 0
    BRAKE_HARD = False
    DENSITY_PERCENTAGE = 0
    PEDESTRIAN_WEIGHT = 0.0
    PEDESTRIAN_RANDOM_LANE_CHANGE_PERCENTAGE = 0
    PEDESTRIAN_LEFT_BIAS_PERCENTAGE = 0
    PENALTY_WAIT = False
    CONSIDER_MINIMUM = False
    SIDE_STRIPS_TO_CONSIDER = 1

    total_types_of_objects = 4.0

    #: Set by the GUI; the headless run never touches it.
    show_progress_slider = None
    simulation_step_line_nos = None

    @classmethod
    def snapshot(cls) -> dict:
        """Copy the whole configuration.

        A run mutates these: the option form converts ``MaximumSpeed`` from
        km/h and transforms ``EncounterPerAccident``, ``along_pedestrian_mode``
        is toggled every 20 steps, and ``GeometryMode`` fills in the median and
        roundabout tables.  Taking a snapshot straight after
        ``Utilities.initialize()`` lets the GUI put all of that back before
        offering the form again, so a second run starts from the same place the
        first one did instead of from the first one's leftovers.
        """
        return {k: (v.copy() if isinstance(v, (dict, list)) else v)
                for k, v in vars(cls).items()
                if not k.startswith("_") and k not in ("snapshot", "restore")}

    @classmethod
    def restore(cls, snap: dict) -> None:
        for k, v in snap.items():
            setattr(cls, k, v.copy() if isinstance(v, (dict, list)) else v)


def scratch_random() -> JavaRandom:
    """The RNG for sites the Java original gave a fresh ``new Random()``.

    Roadside-object placement and parking times, pedestrian arrivals and the
    blockage draws all build a generator per call, seeded from the clock.  That
    is faithful -- but it means ``Parameters.seed`` never controlled them, so a
    run with ``ObjectMode On`` could not be repeated even with the seed pinned,
    and the difference is large: those objects are what the traffic has to
    negotiate around.

    There is no reference stream to protect here, because an unseeded generator
    never had one; only the distributions matter.  So with ``DETERMINISTIC``
    set these draw from the one seeded generator and the run becomes
    repeatable, and without it they behave exactly as they always have.
    """
    if Parameters.DETERMINISTIC and Parameters.random is not None:
        return Parameters.random
    return JavaRandom()
