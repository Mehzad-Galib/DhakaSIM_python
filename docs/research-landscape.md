# DhakaSim: where the research stands

A survey of the seven papers in `DhakaSIM Papers/`, what each one added to the
simulator, what it measured, and where the open problems sit. Written as
background for choosing an MSc thesis topic.

Everything below comes from those seven PDFs. Where a number was legible only
inside a figure rather than in the text or a table, it is left out rather than
guessed at.

## The short version

DhakaSim began in 2014 as a BUET student project and has been rebuilt roughly
every three years since, each time by a new group in the same lab. It is now
called RoadBird in the most recent journal paper, though the older name still
appears in the 2025 work. Six of the seven papers come from the Next-Generation
Computing group at BUET under A. B. M. Alim Al Islam, who is an author on all
of them.

The through-line is one idea: a road is a set of thin strips rather than lanes,
so a vehicle can straddle several and slide sideways between them. Everything
else has been layered onto that.

| Year | Venue | What it added |
| --- | --- | --- |
| 2014 | Conference paper | The simulator itself, strip model, 11 vehicle types |
| 2018 | ICT4S | Pedestrians, hawkers, illegal parking, sensor calibration |
| 2020 | SIMPAT | Polygonal GIS to road network conversion |
| 2024 | IEEE T-ITS | Lane vs non-lane study, three cities, 13 car-following models |
| 2025 | IEEE Access | Side friction: roadside objects and pedestrian classes |
| 2025 | IEEE Access | Multi-objective signal scheduling with NSGA-II |

The seventh PDF is a preprint of the signal scheduling paper and is discussed
alongside the published version.

## Paper by paper

### 2014: the original DhakaSim

*DhakaSim: A Tool for Simulating Traffic Mobility in Dhaka City.* Dev, Parvez,
Bhuiyan, Mosharraf, Khan, Haque, Al Mamun, Chowdhury, Hossain, Morshed, Al
Islam. Conference paper, eleven authors, all BUET.

This is a three-page demonstration paper, and it reads like one. It sets out
the architecture that survives to this day: links contain segments, segments
contain strips, and lateral movement happens strip by strip. Strip width is
0.2 m against a lane width of 2.7 to 4.6 m. Eleven vehicle types are modelled,
including cycle and rickshaw.

Two implementations existed: a standalone Java application and a parallel C++
version. The four input files are the same ones the current code reads, under
slightly different names (`network.txt`, `demand.txt`, `path.txt`,
`paralib.txt`).

The only result shown is a sample network of 8 nodes and 13 links run at two
demand levels, 1X and 1.5X. Average speed drops and average waiting time rises
across nearly every link. There is no validation against field data.

### 2018: pedestrians, hawkers, and the first calibration

*Towards Simulating Non-lane Based Heterogeneous Road Traffic of Less Developed
Countries.* Alam, Sarker, Biswas, Zubaer, Toha, Nurain, Al Islam. ICT4S 2018,
EPiC Series in Computing vol. 52, pp. 37 to 48.

The first paper to take validation seriously. It adds the behaviours that make
Dhaka traffic distinctive rather than merely dense: pedestrians crossing
wherever they like, hawkers walking along the carriageway instead of the
footpath, and illegally parked vehicles.

Calibration data was collected with an ultrasonic sensor module (HC-001)
measuring traffic flow rate and average vehicle speed. Against that data the
simulator reached 99% accuracy for travel time between two Dhaka intersections.

Treat that 99% carefully. It is one route between two intersections, not a
network-wide claim, and the later papers report much more conservative accuracy
once they measure more routes.

### 2020: getting road width out of a map

*Towards simulating non-lane based heterogeneous road traffic of less developed
countries using authoritative polygonal GIS map.* Zubaer, Alam, Toha, Salim, Al
Islam. Simulation Modelling Practice and Theory 105 (2020), article 102156.

The most technically self-contained paper of the set, and the one most useful
if you care about building networks.

Its argument is that polyline GIS data, which is what OpenStreetMap gives you,
records where a road runs but not how wide it is. Without width you cannot know
a road's capacity, and for a strip-based simulator width is the single most
important input, since it decides how many strips a road has. Their answer is
to parse *polygonal* GIS data, where roads are closed shapes, and recover both
length and width from the geometry.

They give the algorithm, analyse its time complexity, validate the theoretical
runtime against measured runtime, and tune its parameters. Parsing the
authoritative GIS street maps of four areas of Dhaka gave 86% accuracy on
average. After calibration the simulator again reached 99% accuracy on travel
time.

The 86% figure is worth remembering. Automatic map conversion is not a solved
problem, and roughly one road in seven comes out wrong.

### 2024: the lane versus non-lane question

*"To Lane or Not to Lane?" Comparing On-Road Experiences in Developing and
Developed Countries Using a New Simulator RoadBird.* Mushfiq, Toha, Salim,
Mostak, Rahaman, Al-Nabhan, Sadri, Al Islam. IEEE Transactions on Intelligent
Transportation Systems, vol. 25 no. 8, August 2024, pp. 8486 to 8498.

The flagship paper, and the one with the clearest research question: what would
happen if Dhaka's unstructured traffic were forced into lanes, and what would
happen if a developed city's lane traffic were let loose?

The simulator is renamed RoadBird here. It gains thirteen car-following models
and four discretionary lane-changing models, so that the answer does not depend
on one behavioural assumption. Road topologies for Dhaka, Miami and Riyadh were
extracted from those cities' GIS maps.

Validation is in Table IV, which compares simulated travel times against
observed ones on two Dhaka routes (Shankar to Palashi and back) for cars and
motorbikes at three densities. Twelve cases, each with a t-test and a
Kolmogorov-Smirnov test. Nine of the twelve have p above the 5% threshold,
meaning the simulated and observed distributions cannot be told apart. Accuracy
by MAPE runs from 82% to 95% with a mean of 88%. By RMSPE, which punishes
outliers harder, the mean is 85% with a best case of 94% and two cases falling
to 75%.

The findings, in the paper's own framing:

- On Dhaka's topology, non-lane traffic beats lane traffic on link speed,
  waiting time and flow rate, at every generation rate tested. Average link
  speed stays below 10 km/h, consistent with the World Bank figure of about
  7 km/h for the city.
- Speed falls and waiting time rises as the generation rate increases, for both
  systems.
- Switching the vehicle mix from heterogeneous to homogeneous, which is to say
  removing the slow human-powered vehicles, improves both systems substantially.
- Although non-lane performs better at low generation rates, lane-based
  networks overtake it at high generation rates on average vehicle speed. This
  is the one crossover the paper predicts.
- On Riyadh's topology, lane-based traffic wins on flow rate and waiting time.
  The reason given is physical: Riyadh's roads are wider and longer, so they
  can hold more vehicles at high speed for a sustained stretch, and the wider
  roads let non-lane traffic make more lateral movements, which hurts it.
- Pedestrian crossings barely affect heterogeneous traffic but slightly slow
  homogeneous traffic.

The conclusion is deliberately hedged: converting Dhaka to lanes does not
guarantee improvement, because the vehicles are small, slow and human-powered
and the roads are narrow. Wide-road networks do benefit from lanes.

### 2025: side friction

*A Tale of Side Friction Elements in Dhaka City: From Modeling Real Cases to
Revealing Impacts Through Event-Based Simulation.* Rumi, Nobel, Haque, Fahmid,
Toha, Al-Nabhan, Alnamlah, Al Islam. IEEE Access vol. 13, 2025, from p. 37344.

Side friction is anything beside or on the road that disrupts flow. This paper
identifies the elements that matter most in Dhaka from field data, models them,
and puts them into the simulator.

Seven element types across three groups: parked cars, parked rickshaws and
parked CNGs; standing pedestrians, pedestrians walking along the road, and
pedestrians crossing it; and non-motorised vehicles.

The headline results are large. Side friction can cut Dhaka's average speed by
up to 36.55% and raise average waiting time by up to 161.3%. Pedestrians do
considerably more damage than any other element, and road-crossing pedestrians
are the single largest source of congestion.

The finding about non-motorised vehicles is the interesting one, because it
cuts both ways. They slow the network's average speed, but they keep the flow
rate high, because they use Dhaka's limited road space efficiently. Removing
rickshaws would make the city faster and lower its throughput at the same time.

Validation is against Google Maps data.

Note the framing difference against the 2024 paper. That one found pedestrians
barely affected heterogeneous traffic. This one finds pedestrians are the
leading cause of congestion. The two are not necessarily in conflict, since
they model pedestrians at different levels of detail and at different densities,
but anyone building on this work should know the two papers disagree in tone
and should check which pedestrian parameters they are running with.

### 2025: multi-objective signal scheduling

*Toward Devising A Multi-Objective Traffic Signal Scheduling Approach for
Non-Lane-Based Heterogeneous Traffic.* Rahaman, Rumi, Islam, Toha, Mushfiq,
Rahman, Nayeem, Al-Nabhan, Al Islam. IEEE Access vol. 13, 2025, from
p. 172598, published 9 October 2025.

Almost all signal scheduling research assumes lane-based homogeneous traffic.
This paper asks what an optimal schedule looks like when neither assumption
holds.

It uses NSGA-II, a multi-objective genetic algorithm, and contributes two
objective functions built around the heterogeneous nature of the traffic
together with a vehicle priority model. Both binary-encoded and real-encoded
populations were tried; real encoding performed better on a numerical
simulator, and that version was then integrated into DhakaSim itself.

Simulations ran over the same three networks as the 2024 paper: Dhaka, Miami
and Riyadh. Against the fixed-time schedules currently used in parts of Dhaka,
the method raised average speed by 163.1% and cut average waiting time by 56.1%
across Dhaka's network during peak hours. Miami and Riyadh also improved, which
is the paper's evidence that the approach generalises.

The seventh PDF, *A Multi-Objective Approach for Traffic Signal Scheduling
Leveraging Vehicle Priority Model and Microscopic Simulation*, is a preprint of
this work from April 2025 with a partly different author list. It reports
146.9% and 66.4% for the same two figures. Cite the published version.

## What the code in this repository already does

Useful to know before picking a topic, because it changes what is cheap and
what is expensive.

This repository is a Python port of the Java original, with Java arithmetic
parity pinned by tests. It implements the strip model, thirteen car-following
models including a modified Newtonian one, four discretionary lane-changing
models, the roadside objects and all three pedestrian classes from the side
friction paper, traffic signals, and a 3D view.

Beyond the papers it adds a GeoJSON to network converter with dual carriageway
fusion, a seeded reproducibility mode, a parallel experiment harness, the five
error measures plus t-test and Kolmogorov-Smirnov test with no SciPy
dependency, and an HTML report that compares a sweep against the 2024 paper's
claims.

The 2025 scheduling work is ported too: `dhakasim/signal_schedule.py` carries a
real-encoded NSGA-II, both versions of the paper's objective functions, its
numerical simulator for scoring candidates, and the fixed-time and
biased-random baselines it compares against. What it does *not* reproduce is
the paper's result -- version 2 of the objective functions is the weaker of the
two here, not the stronger. README has the numbers and the mechanism.

What is missing relative to the papers:

- No polygonal GIS parser. The converter reads polyline GeoJSON, which is the
  format the 2020 paper argues against, and infers width from tags rather than
  geometry.
- The Miami and Riyadh networks here are idealised grids carrying the paper's
  stated road widths, not the real extracted topologies.
- No field travel-time data, so the Table IV validation cannot be reproduced.

## Directions worth a thesis

The 2024 paper states its own future work explicitly: richer parameters
covering traffic safety, road intersections, signalling and pedestrians; a
combined setup with lane and non-lane systems in the same topology; and
evacuation scenarios where demand far exceeds capacity. Those are the authors'
words and are the safest ground to build on, since a reviewer can see the
lineage.

The rest of this section is my own reading of where the gaps are.

### Mixed lane and non-lane in one network

The authors named this and nobody has done it. Today strip width is a single
global setting, so a network is entirely lane-based or entirely not. Making it
a per-link property would let you ask a policy question that actually matters:
if you can only afford to enforce lane discipline on some corridors, which ones,
and how much do you gain?

This is a strong thesis shape. The modelling change is modest, the search space
over which links to convert is large enough to be interesting, and it connects
naturally to the signal scheduling work if you optimise both together. The
2024 paper's own finding that wide roads reward lanes and narrow ones do not
gives you a hypothesis to test rather than an open-ended search.

### Evacuation and demand beyond capacity

Also named by the authors, and unusually well suited to this simulator, because
non-lane behaviour is exactly what emerges when a road system is overwhelmed.
Contraflow and lane reversal are the standard interventions and the paper
speculates that non-lane strategies may beat them.

Be careful about one thing. Every result in these papers is at demand levels
where the network still functions. Pushing far past capacity will exercise
parts of the model that have never been validated, so part of the work has to
be establishing that the simulator behaves sensibly under gridlock before any
result from it means anything.

### Safety rather than throughput

Every paper here measures speed, waiting time and flow. None of them measures
safety, even though the simulator already counts vehicle-pedestrian and
vehicle-vehicle encounters and has a time-to-collision threshold parameter.

Given that road-crossing pedestrians are the leading cause of congestion in the
2025 side friction paper, and that pedestrian fatalities are a serious problem
in Dhaka, a surrogate safety study is an obvious and socially useful gap. The
interesting question is whether the interventions that improve throughput make
pedestrians safer or more exposed, since it is entirely plausible that faster
traffic is more dangerous traffic. A result showing that speed and safety pull
in opposite directions would be worth publishing.

### Automatic calibration

Every one of these papers calibrates by hand and reports the accuracy it got.
Nobody has built a systematic procedure. The pieces are already here: five
error measures, two hypothesis tests, a parallel sweep harness and a
reproducible seeding mode. What is missing is an optimiser over the parameter
space and, crucially, observed data to fit against.

This is lower risk than the other options and makes a genuine methodological
contribution, since the honest complaint about all simulator papers of this
kind is that the calibration is bespoke and unrepeatable. It pairs well with a
second contribution rather than standing alone.

### Emissions and fuel

Nothing in this line of work models emissions, and the fleet makes it
interesting: a traffic stream that is part human-powered, part two-stroke and
part diesel does not behave like a Western fleet under any standard emissions
model. The rickshaw finding from the side friction paper sets up the question
directly, since rickshaws lower average speed but raise throughput, and slow
dense traffic is not automatically dirtier traffic when a large share of it
emits nothing at all.

This would require an emissions model the project does not have, which is the
main risk, but it opens a different publication audience.

### Finishing the GIS pipeline

The 2020 paper's polygonal parser reached 86% and was never, as far as these
papers show, applied beyond four areas of Dhaka. Meanwhile the 2024 paper
extracted three cities and the networks are small. Nobody has run this
simulator at the scale of a whole city.

There are two separable problems: improving the conversion accuracy, and
finding out whether the simulator's performance holds up over a network of
thousands of links. The second is a real question, since the current runs take
tens of minutes for 23 links.

### Adaptive signal control

The 2025 work optimises fixed schedules offline. The natural follow-up is
signals that respond to what is actually on the road, whether by a simple
responsive rule or by reinforcement learning. Two things make it defensible
here rather than derivative of the large existing literature on RL traffic
signals: that literature is almost entirely lane-based and homogeneous, and
side friction is now modelled, so a controller can be tested against parked
vehicles and jaywalking rather than an idealised road.

The risk is that RL traffic signal control is a crowded field, so the novelty
has to rest on the non-lane heterogeneous setting, not on the method.

## Where to publish

The group's own venues first, since acceptance there is evidence the topic fits.

For journals, IEEE Transactions on Intelligent Transportation Systems is the
strongest target and took the RoadBird paper, but expect a long review cycle;
the 2024 paper was submitted in November 2022 and published in June 2024.
IEEE Access took both 2025 papers and is much faster and open access, at the
cost of prestige. Simulation Modelling Practice and Theory published the GIS
paper and is the right home for anything where the contribution is the
simulation method itself rather than a transport finding.

Beyond those, Transportation Research Part C is the leading venue for
simulation and emerging transport technology and would suit the mixed lane
or evacuation work. Journal of Advanced Transportation and IET Intelligent
Transport Systems are reasonable mid-tier options. Transportmetrica A or B
suits methodological work. Physica A has a long history of publishing
car-following and traffic flow models, which is where a new behavioural model
would fit; several of the models implemented in this simulator were originally
published there or in Physical Review E.

For conferences, IEEE ITSC is the main annual venue in this field. The
Transportation Research Board Annual Meeting matters if you want transport
engineers rather than computer scientists to read it. The Winter Simulation
Conference suits the calibration and methodology work. ICT4S published the 2018
paper and would fit anything with a sustainability or emissions framing.
Regionally, ICCIT and NSysS are worth considering for early-stage work,
particularly to get feedback before committing to a journal submission.

One practical note on strategy. A thesis that produces a validated simulator
extension plus one policy finding gives you two papers: a methods paper in
SIMPAT or IEEE Access, and an applied paper in T-ITS or TR Part C. Splitting
that way is common in this group's own record and is easier than trying to
place a single paper that does both.

## Caveats

The 2024 paper's per-link figures could not be extracted as text, because the
PDF stores them in compressed object streams. Only values stated in prose or in
Table IV are quoted above.

Where two papers report different numbers for the same thing, both are given
rather than one being silently preferred.

None of the accuracy figures in these papers should be read as a general
statement about the simulator. Each is measured on a small number of routes in
one city, and the 2020 and 2018 papers' 99% figures in particular rest on far
less data than the 2024 paper's more conservative 88%.
