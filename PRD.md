# EPIDEMIC OUTBREAK SIMULATION PLATFORM (EOSP)
## Comprehensive Product Requirements Document
**For Agentic Software Builder Execution**

---

# EXECUTIVE SUMMARY

## Product Definition

**EOSP** is a production-grade epidemiological forecasting platform that models the real-time evolution of the May 2026 Andes hantavirus outbreak aboard MV Hondius and its subsequent geographic spread. The system ingests continuously-updated outbreak data, re-estimates transmission parameters via Bayesian inference, and generates probabilistic forecasts through Monte Carlo agent-based simulation.

### Core Value Proposition

- **Real-time parameter inference**: Updates transmission rates as new cases confirm
- **Scenario-based forecasting**: Model impact of quarantine, evacuation timing, protocol changes
- **Transparency**: Version-controlled, auditable inference pipeline
- **Epidemiologist-focused**: Designed for public health decision-makers, not the general public

### Target Deployment Context

- **Primary users**: National health authorities (UK HSA, South Africa Health, Swiss health ministry, Spanish health authority)
- **Secondary users**: WHO coordination center, CDC epidemiologists, cruise line operators
- **Update cadence**: 12-hour inference cycles + immediate updates on new confirmed cases
- **Forecast horizon**: 14-day rolling window with 95% credible intervals

### Key Success Criteria

1. Model can **retrodict** observed 8 cases with <2σ error
2. New case ingestion → forecast update in <30 minutes
3. Inference remains **stable** (Rhat <1.01, divergences <50 per 2000 draws)
4. Dashboard updates in real-time via WebSocket (latency <2s)
5. All model versions auditable with complete data lineage

---

# PROBLEM STATEMENT & CONTEXT

## Outbreak Facts (As of May 6, 2026)

**Case Data:**
- 8 confirmed/suspected cases across 4 countries; 3 confirmed deaths; symptom onset April 6–28, 2026
- Andes strain confirmed (human-to-human transmissible)
- Transmission window ~1 day
- CFR 35–50%
- Cases evacuated to: South Africa → Switzerland, Netherlands, Canary Islands
- Ship traveling Cape Verde to Tenerife (3.5-day voyage)

**Key Uncertainties:**

1. **How many asymptomatic/unreported cases on ship?** (Observed: 8, likely: 12–20)
2. **Transmission probability among close contacts?** (Literature: 5–15%, needs calibration)
3. **Will evacuees cause secondary clusters at destination?** (Depends on isolation protocols)
4. **Did rodent contamination occur on ship or pre-boarding?** (Affects model baseline)

**Why This System is Needed:**

- Manual tracking → **slow**, inconsistent estimates
- Each health authority makes independent assumptions → **conflicting advice**
- No transparent mechanism to **learn from emerging data** → public trust erosion
- Decision-makers need **scenario analysis** to optimize quarantine/evacuation trade-offs

---

# PRODUCT VISION & STRATEGIC GOALS

## Vision Statement

"An authoritative, continuously-learning epidemiological intelligence platform that transforms raw outbreak data into actionable forecasts for global health authorities."

## Strategic Goals (Ranked by Priority)

### Goal 1: Credibility & Trust (Months 1–3)
- **Objective**: Model *retrospectively* reproduces observed 8 cases
- **Metric**: 95% credible interval of model contains observed case count at each time-point
- **Why**: Without retrodiction, forecasts have zero credibility
- **Implementation**: Daily model validation against known cases; publish "hindcast accuracy" report

### Goal 2: Real-time Intelligence (Month 2 Ongoing)
- **Objective**: 30-minute data-to-forecast pipeline
- **Metric**: Median latency from case report → dashboard update <30min
- **Why**: Decision windows are tight; delays render forecasts obsolete
- **Implementation**: Kafka pipeline, vectorized inference, Redis caching

### Goal 3: Scenario Clarity (Month 2–3)
- **Objective**: Enable comparison of 4+ policy interventions
- **Scenarios**: 
  - Baseline (status quo)
  - Immediate quarantine (all ship passengers isolated)
  - Evacuation delay (+3, +7 days)
  - Destination interventions (contact tracing in Tenerife/Amsterdam/Zurich)
- **Metric**: Decision-makers can quantify trade-offs (e.g., "delaying evacuation by 3 days reduces cases in Tenerife by 40% with 95% CI: 20–65%")

### Goal 4: Operational Excellence (Months 1–6)
- **Objective**: Platform is maintained autonomously for 6+ months post-launch
- **Metrics**:
  - 99.5% API uptime (SLA)
  - All model versions auditable
  - Automatic alerts for data anomalies
  - Rollback capability without manual intervention

---

# USER PERSONAS & USE CASES

## Primary Personas

### Persona 1: Public Health Epidemiologist
**Name**: Dr. Sarah Chen (WHO Coordinator)  
**Goals**: 
- Understand outbreak trajectory with quantified uncertainty
- Compare intervention strategies  
- Communicate risk to policymakers with credible intervals

**Pain Points**:
- Different countries running private models with inconsistent R₀ estimates
- No shared ground truth → contradictory advice

**Interaction Pattern**:
- Log in 2–3x daily
- Adjust parameters manually (test assumptions)
- Export forecasts for briefing documents
- Flag anomalies in data

### Persona 2: Emergency Response Manager
**Name**: Tom Davies (UK Health Security Agency)  
**Goals**:
- Quick answer: "By what date will we exceed 20 cases in UK?"
- Understand which interventions reduce spread fastest

**Pain Points**:
- No intuitive "what-if" interface
- Long wait for epidemiologist to run custom scenarios

**Interaction Pattern**:
- Uses dashboard via mobile/tablet
- Clicks scenario buttons, reads summary cards
- Exports 1-page briefing
- Calls epidemiologist with targeted questions

### Persona 3: Data Scientist / AI Researcher
**Name**: Alex Kim (CDC Emerging Infectious Diseases)  
**Goals**:
- Access raw inference traces, posterior samples
- Validate methodology
- Publish methodology in Lancet Infectious Diseases

**Pain Points**:
- Proprietary black-box tools prevent academic validation
- Can't replicate other agencies' models

**Interaction Pattern**:
- Programmatic API access (notebooks, Python)
- Downloads posterior samples as NetCDF (via ArviZ)
- Compares multiple model versions
- Comments on methodology in GitHub issues

### Persona 4: System Administrator
**Name**: James Rodriguez (Health Authority IT)  
**Goals**:
- Uptime, security, compliance
- On-prem or hybrid cloud deployment
- HIPAA/GDPR compliance

**Pain Points**:
- SaaS tools may not meet data sovereignty requirements
- Audit trails insufficient for medical IT

**Interaction Pattern**:
- Configures data sources, API keys
- Monitors dashboards (Grafana)
- Manages user access, logs
- Performs monthly security audits

---

## Critical Use Cases

### Use Case 1: Daily Situation Report (Epidemiologist)
**Actor**: Dr. Sarah Chen  
**Trigger**: 09:00 UTC, new cases potentially reported overnight  
**Flow**:
1. Opens dashboard
2. Sees "Data updated 30 min ago, v1.4.2" badge
3. Views case count trajectory with 95% CI band
4. Compares to previous day's forecast (hindcast accuracy metric)
5. If major deviation, investigates: clicks "Data Quality Report" to see which cases are suspicious
6. Reviews "Parameter Changes" table (old vs new p_transmit, etc.)
7. Generates PDF: "Daily Forecast Report 2026-05-07"
8. Distributes to national coordinators

**Success Metrics**:
- Takes <10 minutes
- Confidence intervals support decision-making
- No "surprises" (model should not suddenly predict 3x more cases)

---

### Use Case 2: Scenario Analysis (Emergency Manager)
**Actor**: Tom Davies  
**Trigger**: Policy meeting at 14:00; needs impact analysis of delaying evacuation  
**Flow**:
1. Opens "Scenario Explorer" tab
2. Selects baseline scenario, notes "median 12 cases by May 20"
3. Switches to "Evacuation Delayed +7 Days" scenario
4. Reads: "median 18 cases (+50%, 95% CI: +20% to +95%)"
5. Compares to "Quarantine Immediate": "median 5 cases (-60%, 95% CI: -80% to -20%)"
6. Exports 3-panel comparison chart as PNG
7. Uses in 14:00 meeting to justify recommendation

**Success Metrics**:
- Scenarios run within 10 seconds after selection
- Comparison is intuitive (delta, not absolute)
- Confidence intervals are credible (not over-confident)

---

### Use Case 3: Model Validation (Data Scientist)
**Actor**: Alex Kim  
**Trigger**: Publishes methodology paper, needs to validate reproduction  
**Flow**:
1. Accesses `GET /api/v1/inference/v1.4.2` endpoint
2. Downloads posterior samples (2000 draws × 5 parameters) as NetCDF
3. Loads in Python: `trace = arviz.from_netcdf('posterior_v1.4.2.nc')`
4. Computes posterior predictive via ABM re-simulation
5. Compares observed case counts to predictive distribution
6. Publishes figure: "Hindcast Validation for Andes Hantavirus Outbreak"
7. Opens GitHub issue requesting explanation of prior choices

**Success Metrics**:
- Raw posterior samples are publicly accessible (with appropriate lag)
- Full inference code is open-source
- Methodology withstands peer review

---

### Use Case 4: Data Anomaly Detection (Administrator)
**Actor**: James Rodriguez  
**Trigger**: Automatic alert: "Case timeline implausible (symptom after death)"  
**Flow**:
1. Receives Slack alert with case ID, anomaly description
2. Logs into admin panel, views case record
3. Sees flags: "Temporal inconsistency", "Data quality score: 0.42"
4. Views prior version of this case (if updated)
5. Contacts source (e.g., South Africa Health Ministry) to clarify
6. Updates data, case is re-validated
7. Inference automatically re-triggered (cron job in 2 hours, or manual "Refit Now")
8. Dashboard reflects new forecast

**Success Metrics**:
- Alert latency <5 minutes after data ingestion
- Admin can quickly identify data source
- Rollback & re-inference fully automated

---

# FUNCTIONAL REQUIREMENTS

## FR-1: DATA INGESTION & VALIDATION PIPELINE

### FR-1.1: Automated Case Ingestion
**Requirement**: System SHALL continuously ingest outbreak data from multiple sources without manual intervention.

**Sources to support** (priority order):
1. WHO Disease Outbreak News RSS feed (automated, 6-hourly)
2. Google Sheets form submissions (manual epidemiologist entries, real-time on submit)
3. Contact tracing databases (PRIME, EDR, other national systems via API)
4. News/media monitoring (parse structured data, low confidence weight)

**Specifications**:
- Poll WHO feed every 6 hours
- Google Sheets form triggers Zapier → Webhook on submission
- Support basic HTTP API for contact tracing systems: `POST /api/v1/cases` (JWT auth required)
- Each ingestion event SHALL include:
  - Source identifier
  - Raw data (JSON)
  - Timestamp
  - Data quality metadata (confidence score, potential duplicates)

**Implementation Notes**:
- Use **Apache Kafka** or **AWS SQS** as message queue (Kafka preferred for scalability; SQS for simplicity)
- Implement idempotency: same case reported twice should be recognized as duplicate
- Queue SHALL persist data for audit trail (30-day retention minimum)

---

### FR-1.2: Data Validation & Quality Scoring
**Requirement**: System SHALL validate ingested data against predefined rules and assign quality scores.

**Validation Rules**:
1. **Temporal consistency**: symptom_onset ≤ hospitalization_date ≤ death_date
2. **Geographic plausibility**: country/airport codes valid, contact locations within known outbreak geography
3. **Clinical plausibility**: CFR ≤ 50% for Andes hantavirus, symptoms match known presentation
4. **Contact network plausibility**: contacts must be within 2-week window of index case
5. **Duplicate detection**: exact matches on (patient_id, symptom_onset), fuzzy matching for near-duplicates

**Quality Score Formula** (0–1):
```
score = (
  0.30 × is_from_official_source +
  0.25 × (1 - duplicate_probability) +
  0.25 × temporal_consistency +
  0.20 × geographic_plausibility
)
Threshold for ingestion: score ≥ 0.65
```

**Specification**:
- Invalid records SHALL be logged with reason, NOT discarded
- Quality score <0.65 → quarantined for manual review
- Admin dashboard shows validation queue (all cases <0.75 score)
- Each validation check has configurable thresholds (for future tuning)

**Implementation Notes**:
- Implement as Python service with configurable rule engine
- Support validation rule versioning (rules change as domain knowledge evolves)
- Expose validation results via `GET /api/v1/cases/{case_id}/validation` endpoint

---

### FR-1.3: Case Record Persistence (Immutable Audit Trail)
**Requirement**: System SHALL maintain complete history of all case data with immutability guarantees.

**Database Design**:
- Table: `case_records` (current state)
- Table: `case_records_history` (audit trail, append-only)
- Table: `case_validations` (validation results, linked to history)

**Key Fields** (case_records):
```
- case_id (UUID, primary key)
- patient_identifier (anonymized if PII)
- symptom_onset_date (DATE)
- hospitalization_date (DATE, nullable)
- death_date (DATE, nullable)
- location_country (VARCHAR)
- location_airport_code (VARCHAR, nullable)
- confirmed_or_suspected (ENUM)
- lab_test_result (ENUM: PCR_positive, PCR_negative, serology_positive, not_tested)
- contacts (JSON array of contact objects)
- data_source (VARCHAR: WHO_DON, Manual_Form, Contact_Trace_DB)
- ingestion_timestamp (TIMESTAMP)
- validation_score (FLOAT)
- version (INTEGER, auto-increment)
- updated_by (VARCHAR, user/system identifier)
- updated_reason (VARCHAR: New_case, Lab_confirmed, Correction, Reassessment)
```

**Immutability Guarantees**:
- Updates NEVER modify existing rows; instead create new row in history table
- Historic versions can be queried: `case_records_history.version = 3`
- Rollback capability: SELECT from history, mark current as superseded
- Full lineage: WHO reported case → Lab confirmed → Contact traced → Epidemiologist corrected

---

## FR-2: BAYESIAN INFERENCE ENGINE

### FR-2.1: Adaptive Parameter Estimation
**Requirement**: System SHALL estimate epidemiological parameters using Bayesian methods, updating priors as new data arrives.

**Parameters to Estimate**:

| Parameter | Symbol | Prior | Interpretation | Sensitivity |
|-----------|--------|-------|-----------------|-------------|
| Transmission probability (per close contact) | p_transmit | Beta(1.5, 20) | ~7% mean, wide range | HIGH |
| Contacts per person per day | contacts_daily | Poisson(2.5) | Cruise ship density | MEDIUM |
| Incubation period (days) | incubation | Gamma(2, 0.25) | 8-day mean | MEDIUM |
| Symptom-to-severe interval | symptom_to_severe | Weibull(1.5) | Rapid progression | LOW |
| H2H transmission relative to contact baseline | h2h_multiplier | LogNormal(0, 0.5) | If close contact → multiply p_transmit by this | HIGH |

**Inference Specification**:

1. **Model Architecture**:
   - Use **PyMC3** or **Numpyro** (Numpyro preferred for speed + GPU support)
   - Hierarchical model: priors encode literature values; likelihood updates from observed cases
   - Likelihood function: Poisson-distributed case count given contact network

2. **Priors**:
   - **First run**: Weakly informative priors from literature (Beta, Gamma distributions)
   - **Subsequent runs**: Previous posterior becomes informative prior (hierarchical)
   - Example: If v1.0 estimated p_transmit ~ Normal(0.078, 0.035), then v1.1 prior is Normal(0.078, 0.035)

3. **Inference Algorithm**:
   - Use HMC (Hamiltonian Monte Carlo) via NUTS sampler
   - Target: 2000 draws, 1000 warmup samples
   - Parallel cores: 4 (CPU) or 1 GPU chain (TPU if available)
   - Convergence criteria: Rhat < 1.01 on all parameters

4. **Output**:
   - Posterior trace object (ArviZ InferenceData)
   - Summary statistics: mean, std, 2.5th/97.5th percentiles
   - Diagnostics: Rhat, effective sample size, divergence count
   - Save as NetCDF for reloading + archival

**Implementation Notes**:
- Inference runs on **AWS Lambda** (ephemeral) or **dedicated EC2** (persistent)
- Expected runtime: 15–30 minutes for full inference (including warmup)
- Vectorize likelihood computation (NumPy operations on entire case timeline)
- Store posterior in S3 + TimescaleDB (temporal metadata)

---

### FR-2.2: Inference Triggers & Scheduling
**Requirement**: System SHALL re-estimate parameters on defined triggers with proper versioning.

**Trigger Types** (Priority-ordered):

1. **Anomaly Trigger** (Real-time, highest priority)
   - If observed cases exceed 3σ from previous forecast → trigger immediate refit
   - Condition: `observed_count > forecast_median + 3 * forecast_std`
   - Latency: Check every 30 minutes
   - Alert level: CRITICAL (email all epidemiologists)

2. **New Case Trigger** (Conditional)
   - If new case added AND validation_score ≥ 0.80 → trigger refit in 30 minutes
   - Rationale: Allow time for duplicate detection, but not too long
   - Cumulative: If 2+ cases arrive before refit executes, include all in single inference

3. **Cron Trigger** (Scheduled)
   - Batch refit every 12 hours (06:00 and 18:00 UTC)
   - Includes all validated cases (regardless of arrival time)
   - Ensures systematic update even if new cases are sparse

4. **Manual Trigger** (On-demand)
   - Epidemiologists can click "Refit Now" button in admin panel
   - Requires auth; logged in audit trail
   - Useful for testing assumptions (e.g., "what if we exclude suspect cases?")

**Specification**:
- Each inference run gets unique version ID: `v{major}.{minor}.{patch}-{timestamp}`
  - Example: `v1.2.1-20260507T143200Z`
- Store metadata with each version:
  - Trigger type
  - Number of cases included
  - Execution time
  - Convergence diagnostics (Rhat, divergences)
  - Data quality score (mean validation_score of included cases)
- Implement version comparison: automatically show parameter deltas vs previous version

---

### FR-2.3: Model Validation & Hindcast Accuracy
**Requirement**: System SHALL validate model against observed data and report accuracy metrics.

**Validation Strategy**:

1. **Temporal Cross-Validation**:
   - For each day t ∈ [Day 1, Day 14]:
     - Train model on cases with symptom_onset ≤ t
     - Generate 1-week forecast (t+1 to t+7)
     - Compare forecast vs observed cases that actually occurred
   - Metric: Mean prediction interval coverage probability (PICP)
     - Should be ~95% for 95% credible intervals
     - Flag if <90% (underconfident) or >98% (overconfident)

2. **Posterior Predictive Checks**:
   - Generate 1000 synthetic case timelines from posterior
   - Overlay with observed case timeline
   - Visual: Does observed fall within posterior predictive envelope?
   - Metric: Observed log-likelihood vs posterior predictive log-likelihood

3. **Out-of-Bag Validation** (For future data):
   - Hold-out last 2 cases from inference
   - Train on first 6 cases, forecast last 2
   - Report whether held-out cases fall within 95% CI

**Reporting**:
- Automatic daily report: "Hindcast Accuracy Report for v1.4.2"
- Accessible via dashboard: "Model Validation" tab
- Shows:
  - Temporal trajectories: forecast band + observed cases
  - PICP metric (should be 0.95)
  - Prediction interval width (narrow = overconfident; wide = conservative)
- Alert if validation metrics degrade (PICP <0.90)

**Implementation Notes**:
- Implement as scheduled job (daily, 02:00 UTC after nightly refit)
- Generate plots via Matplotlib/Plotly, save to S3
- Store metrics in TimescaleDB for trending

---

## FR-3: AGENT-BASED SIMULATION ENGINE

### FR-3.1: Contact Network Modeling
**Requirement**: System SHALL construct realistic contact networks that reflect transmission routes on ship + flights.

**Network Components**:

1. **Ship Contact Graph**:
   - Nodes: 147 passengers + crew (anonymized: P001–P147)
   - Edges: Contact relationships (directed, weighted by contact duration/type)
   - Edge types:
     - **Household**: Shared cabin (high contact, daily, multiple hours)
     - **Workplace**: Shared crew duties (very high contact, 8+ hours)
     - **Social**: Dining, common areas (medium contact, 1–2 hours)
     - **Transient**: Hallways, elevators (low contact, brief)
   - Weights:
     - Household: 0.80 (80% chance of transmission per contact period)
     - Workplace: 0.85
     - Social: 0.40
     - Transient: 0.05
   - **Temporal aspect**: Contacts are active only on days both people are on ship

2. **Evacuation Sub-network**:
   - Models flights from ship:
     - Flight 1: Johannesburg (April 25) - 30 passengers, 2 confirmed cases
     - Flight 2: Cape Verde → Amsterdam (May 3) - 40 passengers
     - Flight 3: Cape Verde → Tenerife (May 5) - 50 passengers, 2 crew
   - Seat density: 0.6 (60% occupancy) → assume contacts among neighbors
   - Contacts during flight: 8–12 hours per passenger

3. **Destination Contact Networks** (Post-evacuation):
   - Hospital networks (Johannesburg, Geneva, Amsterdam, Tenerife)
   - Healthcare workers (doctors, nurses, janitors)
   - Family contacts (spouses, children)
   - Initial data: Manual input from contact tracing; updated as reports arrive
   - Modeling approach: Small-world network (scale-free with clustering)

**Specification**:
- Represent as NetworkX DiGraph (Python)
- Store metadata on edges: contact_type, duration, temporal_window
- Support dynamic updates: add new edges as contact tracing data arrives
- Export formats: GML (GraphML), JSON for visualization

**Implementation Notes**:
- Build ship network from known cabin assignments + crew rosters
- Obtain from Oceanwide Expeditions (public info or FOIA request)
- Seed initial contact graph from WHO contact tracing reports
- Allow manual overrides for epidemiologists (upload CSV of contacts)

---

### FR-3.2: ABM Simulation Mechanics
**Requirement**: System SHALL execute agent-based model that simulates case transmission given contact network and inferred parameters.

**Agent States** (SEIR + D):
- **S (Susceptible)**: Not infected, can acquire virus
- **E (Exposed)**: Infected but not yet symptomatic (incubation period)
- **I (Infectious)**: Symptomatic, can transmit
- **R (Recovered)**: No longer infectious (survivor)
- **D (Deceased)**: Fatal outcome

**State Transitions**:

```
S → E: Contact with infectious person (I)
  Probability: p_transmit × contact_weight
  
E → I: Incubation complete
  Timing: Drawn from Gamma(shape=2, rate=0.25) → ~8 days
  
I → R or I → D: Recovery or death
  Probability of death: CFR = 0.40 (40%)
  Time to outcome: Drawn from Weibull(shape=1.5)
  
Duration of I: ~1 day (transmission window), then move to R/D
```

**Simulation Algorithm** (Daily timestep):

```
FOR each day d in [day_0, day_14]:
  FOR each infectious agent i in I state:
    FOR each susceptible agent s in contact_network[i]:
      IF random() < p_transmit × edge_weight:
        s → E (update state, draw incubation_period)
      
  FOR each exposed agent e in E state:
    IF incubation_period[e] expired:
      e → I (update state, set transmission_window)
  
  FOR each infectious agent i in I state:
    IF transmission_window[i] expired:
      IF random() < CFR:
        i → D
      ELSE:
        i → R
  
  # Record daily statistics
  record_counts: S, E, I, R, D
  record_locations: where are current I agents?
```

**Specification**:
- Implement in **Mesa** (Python ABM framework) or **AnyLogic** (more industrial, paid)
- Use NumPy for vectorized state transitions (faster than agent-by-agent loop)
- Support stochasticity: same parameters, different random seeds → different trajectories
- Runtime: Single trajectory on CPU ~100ms; 10,000 trajectories in parallel ~60 seconds on 16 cores

---

### FR-3.3: Scenario Parameterization
**Requirement**: System SHALL support multiple intervention scenarios by modifying network topology or transmission parameters.

**Baseline Scenario**:
- No interventions
- Full contact network (ship + all flights)
- p_transmit as inferred from Bayesian model
- Output: Median 12–15 cases by May 20

**Scenario 1: Immediate Quarantine** (Day 1)
- All ship passengers isolated in cabins
- Contact network reduced: only household contacts retained
- p_transmit reduced by 90% (isolation protocols)
- Flight contacts reduced: 6-seat radius instead of full cabin
- Expected outcome: Median 3–5 cases (70% reduction)

**Scenario 2: Evacuation Delay** (+3 days, +7 days)
- Ships remains in quarantine for 3/7 days before flights
- Transmissions continue during delay (confined space, poor ventilation)
- After delay, evacuate as in baseline
- Expected outcome: +30% to +100% more cases than baseline

**Scenario 3: Enhanced Destination Protocols**
- Upon arrival, all passengers tested + isolated pending results
- Contacts tested + monitored for 21 days
- Assumes perfect isolation (p_transmit → 0 after arrival)
- Expected outcome: Contains spread to ~8 cases (baseline cases + minimal local)

**Scenario 4: Pharmaceutical Intervention** (Hypothetical)
- Antivirals distributed to all evacuees
- Reduces CFR from 40% to 20%
- Does NOT reduce transmission (only severity)
- Expected outcome: 12 cases still, but 3 instead of 5 deaths

**Scenario 5: Custom** (Epidemiologist-defined)
- Allow manual adjustment of:
  - Contact network (remove/keep specific edges)
  - p_transmit (test sensitivity)
  - CFR (vary by intervention)
  - Quarantine start date
- Result: Custom forecast for specific policy hypothesis

**Specification**:
- Store scenario definitions as JSON configurations
- Each scenario has:
  ```json
  {
    "name": "Evacuation Delay +7 Days",
    "description": "...",
    "network_modifications": {
      "remove_edges": [],
      "modify_weights": {"flight_*": 0.5},
      "add_isolation": ["day_1_to_day_7"]
    },
    "parameter_overrides": {
      "p_transmit": 0.05,
      "cfr": 0.40
    },
    "baseline_for_comparison": "v1.4.2"
  }
  ```
- Pre-compute 5 standard scenarios for fast access
- Support custom scenarios (run on-demand, ~60 sec latency)

---

### FR-3.4: Monte Carlo Ensemble & Forecast Generation
**Requirement**: System SHALL run large ensemble of ABM trajectories to quantify forecast uncertainty.

**Ensemble Specification**:
- **10,000 independent trajectories** per scenario
- Parameter variation:
  - Sample p_transmit from posterior (Bayesian credible interval)
  - Sample contacts_daily from posterior
  - Sample contact network edges from contact tracing uncertainty
- Random seed: Vary to produce different stochastic outcomes
- Aggregation: Percentiles (2.5th, 25th, median, 75th, 97.5th) at each day

**Output Format** (per scenario):

```
{
  "scenario": "Baseline",
  "forecast_horizon_days": 14,
  "timeseries": [
    {
      "day": 1,
      "cases_cumulative": {
        "median": 8,
        "ci_95_lower": 8,
        "ci_95_upper": 8,
        "ci_50_lower": 8,
        "ci_50_upper": 8
      },
      "cases_new": {...},
      "deaths_cumulative": {...}
    },
    ...
    {
      "day": 14,
      "cases_cumulative": {
        "median": 13,
        "ci_95_lower": 7,
        "ci_95_upper": 22,
        "ci_50_lower": 11,
        "ci_50_upper": 15
      },
      ...
    }
  ],
  "metadata": {
    "model_version": "v1.4.2",
    "n_simulations": 10000,
    "parameter_values": {
      "p_transmit_mean": 0.089,
      "p_transmit_std": 0.034,
      "contacts_daily_mean": 2.4,
      "incubation_mean": 8.2
    },
    "execution_time_seconds": 63,
    "generated_timestamp": "2026-05-07T14:32:00Z"
  }
}
```

**Caching Strategy**:
- Cache all forecasts for standard scenarios in Redis (12-hour TTL)
- Cache keys: `forecast:{model_version}:{scenario_name}`
- Custom scenarios: compute on-demand, cache for 1 hour
- Invalidate cache when new model version is generated

**Implementation Notes**:
- Parallelize using Python `multiprocessing.Pool` or AWS Lambda
- Estimate 16-core machine: 10k trajectories in ~60 seconds
- Store full ensemble results (all 10k trajectories) in S3 for post-hoc analysis
- Compute percentiles on-the-fly for API responses (don't pre-compute all percentiles)

---

## FR-4: API & BACKEND SERVICES

### FR-4.1: REST API Specification
**Requirement**: System SHALL expose epidemiological data and forecasts via documented REST API.

**Base URL**: `https://api.eosp.health`  
**Authentication**: OAuth 2.0 (OpenID Connect) or API keys for programmatic access

#### Endpoint: Get Current Case Count
```
GET /api/v1/cases/summary
Response:
{
  "total_confirmed": 3,
  "total_suspected": 5,
  "total_deaths": 3,
  "last_updated": "2026-05-07T14:32:00Z",
  "data_sources": {
    "WHO_DON": 4,
    "Manual_Form": 3,
    "Contact_Trace_DB": 1
  }
}
```

#### Endpoint: Get Case Details
```
GET /api/v1/cases?country=ZA&status=confirmed
Response:
[
  {
    "case_id": "case_20260425_001",
    "patient_id": "anon_p0042",
    "symptom_onset_date": "2026-04-25",
    "location_country": "ZA",
    "confirmed_or_suspected": "confirmed",
    "lab_result": "PCR_positive",
    "validation_score": 0.92,
    "data_quality": "HIGH"
  },
  ...
]
```

#### Endpoint: Get Forecast (Main Forecasting Interface)
```
GET /api/v1/forecasts/{scenario}?model_version=latest
Query params:
  - scenario: "baseline" | "quarantine_immediate" | "evacuation_delay_3d" | "evacuation_delay_7d" | "custom"
  - model_version: version ID (default: latest)
  - include_credible_intervals: true | false (default: true)

Response:
{
  "scenario": "baseline",
  "forecast": [...],  # Timeseries as in FR-3.4
  "metadata": {...},
  "comparison_to_previous_version": {
    "median_case_change": -2,
    "median_case_change_pct": -15,
    "credible_interval_widened": false
  }
}
```

#### Endpoint: Get Inference Results
```
GET /api/v1/inference/{model_version}
Response:
{
  "version": "v1.4.2-20260507T143200Z",
  "timestamp": "2026-05-07T14:32:00Z",
  "parameters": {
    "p_transmit": {
      "mean": 0.089,
      "std": 0.034,
      "ci_95": [0.031, 0.162]
    },
    "contacts_daily": {
      "mean": 2.4,
      "std": 0.8,
      "ci_95": [1.2, 4.5]
    },
    "incubation": {...}
  },
  "diagnostics": {
    "rhat": {...},
    "effective_sample_size": {...},
    "divergences": 23,
    "convergence_status": "CONVERGED"
  },
  "posterior_download_url": "s3://eosp-data/posteriors/v1.4.2.nc"
}
```

#### Endpoint: Model Validation
```
GET /api/v1/validation/hindcast-accuracy?model_version=v1.4.2
Response:
{
  "model_version": "v1.4.2",
  "validation_type": "temporal_cross_validation",
  "results": [
    {
      "training_window_end": "2026-04-10",
      "forecast_window": "2026-04-11 to 2026-04-18",
      "forecast_median": 5,
      "forecast_ci_95": [2, 9],
      "observed": 6,
      "in_credible_interval": true
    },
    ...
  ],
  "picp": 0.94,  # Prediction Interval Coverage Probability
  "picp_target": 0.95,
  "picp_status": "GOOD"
}
```

#### Endpoint: List Model Versions & Compare
```
GET /api/v1/inference/versions?limit=10
Response:
{
  "versions": [
    {
      "version_id": "v1.4.2-20260507T143200Z",
      "timestamp": "2026-05-07T14:32:00Z",
      "trigger": "new_case",
      "n_cases": 8,
      "convergence_status": "CONVERGED",
      "rhat_max": 1.008,
      "divergence_count": 23,
      "data_quality_mean": 0.84
    },
    {
      "version_id": "v1.4.1-20260507T020000Z",
      ...
    }
  ]
}

GET /api/v1/inference/compare?v1=v1.4.2&v2=v1.4.1
Response:
{
  "v1": "v1.4.2-20260507T143200Z",
  "v2": "v1.4.1-20260507T020000Z",
  "parameter_changes": {
    "p_transmit": {
      "v1_mean": 0.089,
      "v2_mean": 0.078,
      "change_pct": 14.1,
      "significant": true
    },
    ...
  },
  "forecast_changes": {
    "median_cases_by_day_14": {
      "v1": 13,
      "v2": 12,
      "change": 1
    }
  }
}
```

#### Endpoint: Scenario Comparison
```
GET /api/v1/scenarios/compare?scenarios=baseline,quarantine_immediate,evacuation_delay_7d
Response:
{
  "comparison_date": "2026-05-07",
  "scenarios": [
    {
      "name": "baseline",
      "cases_by_day_14": {
        "median": 13,
        "ci_95": [7, 22]
      },
      "deaths_by_day_14": {
        "median": 5,
        "ci_95": [2, 9]
      }
    },
    {
      "name": "quarantine_immediate",
      "cases_by_day_14": {
        "median": 5,
        "ci_95": [2, 11]
      },
      "deaths_by_day_14": {
        "median": 2,
        "ci_95": [1, 4]
      },
      "vs_baseline": {
        "case_reduction_pct": -62,
        "death_reduction_pct": -60
      }
    },
    ...
  ]
}
```

#### Endpoint: Data Quality / Anomaly Alerts (Admin-only)
```
GET /api/v1/admin/data-quality/alerts
Response:
{
  "alerts": [
    {
      "alert_id": "alert_20260507_001",
      "severity": "HIGH",
      "case_id": "case_20260427_002",
      "issue": "Temporal inconsistency: symptom_onset after death",
      "timestamp": "2026-05-07T09:15:00Z",
      "recommended_action": "Contact data source for clarification"
    },
    ...
  ]
}
```

---

### FR-4.2: Service Architecture
**Requirement**: System SHALL be composed of loosely-coupled microservices with clear responsibilities.

**Core Services**:

1. **Data Ingestion Service**
   - Responsibility: Fetch from sources, validate, queue for inference
   - Technology: Python + Kafka consumer
   - Triggers: Scheduled (6h), webhooks (Google Forms)
   - Outputs: Validated case records to PostgreSQL + Kafka topic

2. **Inference Service**
   - Responsibility: Bayesian parameter estimation
   - Technology: Python (PyMC3/Numpyro) + JAX
   - Triggers: Cron (12h), new case (conditional), manual request
   - Outputs: Posterior traces to S3 + TimescaleDB metadata

3. **Simulation Service**
   - Responsibility: Run ABM ensembles, generate forecasts
   - Technology: Python (Mesa) + NumPy, parallelized
   - Triggers: On inference completion
   - Outputs: Forecast JSON to Redis + S3

4. **API Service**
   - Responsibility: REST API, query interface
   - Technology: FastAPI (async, Python)
   - Triggers: HTTP requests
   - Outputs: JSON responses, validates auth

5. **Admin & Validation Service**
   - Responsibility: Data quality monitoring, manual case curation
   - Technology: Python + PostgreSQL
   - Outputs: Case validation scores, quality alerts

6. **Monitoring & Alerting Service**
   - Responsibility: Track service health, divergence detection, anomaly alerts
   - Technology: Prometheus + Alertmanager + PagerDuty
   - Outputs: Alerts to Slack, email, SMS

**Service Communication**:
- Synchronous: REST (between API ↔ Database queries)
- Asynchronous: Kafka (data ingestion, cross-service events)
- Data store: PostgreSQL (transactional), TimescaleDB (time-series), S3 (large files), Redis (cache)

**Deployment Strategy**:
- Each service runs in Docker container
- Orchestration: Kubernetes (K8s) or AWS ECS
- Horizontal scaling: Inference & simulation services scale via job queue (Celery or AWS Batch)

---

## FR-5: FRONTEND & DASHBOARD

### FR-5.1: Dashboard Design & Layout
**Requirement**: System SHALL provide epidemiologist-focused dashboard for real-time situation awareness.

**Dashboard Pages** (Tab-based interface):

#### Tab 1: Situation Report (Default Landing)
**Purpose**: Quick overview of outbreak status  
**Components**:
- **Header**: "Outbreak Situation as of 2026-05-07 14:32 UTC"
  - Model version badge: "v1.4.2" (clickable → version history)
  - "Data updated 30 min ago" (auto-refresh every 5 min)
  - "Last refit 8h ago" with refit status indicator
- **Key Metrics Card Grid** (4 cards):
  - Card 1: "Confirmed Cases: 3" (vs 5 suspected) with trend arrow
  - Card 2: "Confirmed Deaths: 3" with case fatality rate
  - Card 3: "Estimated Cases (14-day): 13 [7–22]" with CI range
  - Card 4: "Forecast Status: STABLE" (vs DIVERGING if parameters changed >15%)
- **Main Chart**: Multiline timeseries plot
  - X-axis: Date (April 6 – May 20)
  - Y-axis: Cumulative cases
  - Observed cases: Points with error bars
  - Baseline forecast: Band (95% CI)
  - Previous forecast: Dotted line (for hindcast validation)
- **Data Source Breakdown** (pie chart):
  - % confirmed from WHO vs manual vs contact trace
- **Geographic Map** (Leaflet):
  - Ship icon (current location)
  - Case locations (country-level or airport markers)
  - Flight routes (arrows showing evacuation paths)

---

#### Tab 2: Scenario Explorer
**Purpose**: Comparative analysis of interventions  
**Components**:
- **Scenario Selector** (Buttons):
  - "Baseline" (default)
  - "Quarantine Immediate"
  - "Evacuation Delay +3d"
  - "Evacuation Delay +7d"
  - "+ Custom" (opens form for custom scenario)
- **Comparison View** (Side-by-side):
  - 3-panel layout:
    - Left: Scenario A (cases timeseries + 95% CI)
    - Center: Delta (A vs B, percentage change)
    - Right: Scenario B (cases timeseries + 95% CI)
- **Summary Card**: "Scenario B reduces cases by 62% (95% CI: 40–80%)"
- **Table**: Day-by-day case count for each scenario
- **Export**: Download comparison as PDF or PNG

---

#### Tab 3: Model Performance & Validation
**Purpose**: Transparency in inference quality  
**Components**:
- **Hindcast Accuracy Panel**:
  - Table: Training window end → Forecast window → Observed cases → Predicted CI → "In CI?" (✓/✗)
  - Summary: "PICP = 0.94 (target: 0.95)" ✓ GOOD
- **Parameter Estimation Panel**:
  - Table with columns:
    - Parameter name
    - Current posterior (mean ± std)
    - 95% CI
    - Previous version (for comparison)
    - "Changed?" indicator (if >15% shift)
- **Diagnostic Checks**:
  - Rhat (convergence): All <1.01 ✓ PASS
  - Effective sample size: All >400 ✓ PASS
  - Divergences: 23 (should be <50) ✓ PASS
- **Model Version History** (Table):
  - Version | Timestamp | Trigger | n_Cases | p_transmit | Status
  - Clickable rows → detailed version comparison

---

#### Tab 4: Data Management (Admin-only)
**Purpose**: Curate case data, manage quality issues  
**Components**:
- **Case List** (Filterable table):
  - Columns: Case ID, Symptom Onset, Country, Confirmed/Suspected, Lab Result, Validation Score, Quality
  - Filters: By date, country, status, quality score
  - Sort: By score (ascending), to find suspicious cases
- **Quality Alerts Queue**:
  - List of cases with validation_score <0.75
  - Each alert shows: Case ID, Issue description, Severity
  - Action buttons: "Investigate", "Mark Valid", "Delete"
- **Case Detail Modal**:
  - Full case record view
  - Edit fields (for corrections)
  - Version history (show prior versions)
  - Save → triggers re-validation + re-inference if quality changes >0.15
- **Bulk Import** (CSV upload):
  - Import contact tracing data from health authority systems
  - Preview: Show validation results before commit
  - Confirm: Trigger new inference run

---

### FR-5.2: Real-time Updates via WebSocket
**Requirement**: Dashboard SHALL update in real-time as new data arrives (no page refresh).

**WebSocket Events**:

1. **Case Count Updated**
   ```json
   {
     "type": "CASE_COUNT_UPDATED",
     "data": {
       "total_confirmed": 3,
       "total_suspected": 5,
       "new_case_id": "case_20260507_008"
     }
   }
   ```
   Action: Animate case count card, highlight new case in table

2. **Model Refit Completed**
   ```json
   {
     "type": "MODEL_REFIT_COMPLETED",
     "data": {
       "model_version": "v1.4.2",
       "timestamp": "2026-05-07T14:32:00Z",
       "parameter_changes": {
         "p_transmit": {"old": 0.078, "new": 0.089, "pct_change": 14.1}
       }
     }
   }
   ```
   Action: Update parameter table, flash version badge, show alert banner

3. **Forecast Updated**
   ```json
   {
     "type": "FORECAST_UPDATED",
     "data": {
       "scenario": "baseline",
       "forecast": {...},
       "model_version": "v1.4.2"
     }
   }
   ```
   Action: Animate chart transition (Plotly Scattergl for smooth animation), update scenario cards

4. **Data Quality Alert**
   ```json
   {
     "type": "DATA_QUALITY_ALERT",
     "data": {
       "severity": "HIGH",
       "case_id": "case_20260427_002",
       "issue": "Temporal inconsistency"
     }
   }
   ```
   Action: Add alert to admin panel, notify via Slack

5. **Anomaly Detected**
   ```json
   {
     "type": "ANOMALY_DETECTED",
     "data": {
       "type": "prediction_error",
       "observed": 9,
       "forecast_median": 5,
       "forecast_std": 1.5,
       "sigma_deviations": 2.7
     }
   }
   ```
   Action: Show warning banner, notify epidemiologists via email

---

### FR-5.3: Visualization Components
**Requirement**: Dashboard SHALL use high-quality, interactive charts suitable for scientific communication.

**Chart Library**: Plotly (interactive) + D3.js (advanced)

**Chart Types**:

1. **Timeseries Forecast** (Plotly Scattergl)
   - X: Dates, Y: Cumulative cases
   - Multiple lines: Observed (points), 95% CI band (shaded), 50% CI band (lighter shading)
   - Previous forecast (dotted line for comparison)
   - Hover: Show exact values + credible intervals
   - Interactivity: Zoom, pan, legend toggle

2. **Scenario Comparison** (Plotly bar/line hybrid)
   - X: Scenarios, Y: Median cases at day 14
   - Error bars: 95% CI
   - Color-coded by scenario
   - Hover: Show detailed statistics

3. **Parameter Posterior Distributions** (Plotly histogram or KDE)
   - X: Parameter value, Y: Posterior density
   - Overlay previous posterior (different color)
   - Indicate 95% CI with vertical lines
   - Show point estimates as vertical line

4. **Contact Network Graph** (D3.js force-directed layout)
   - Nodes: Individuals (color by status: S/E/I/R/D)
   - Edges: Contact relationships (thickness by contact weight)
   - Interactive: Click node → highlight neighbors, show details
   - Animate over time: Show disease progression as nodes change color
   - Subgraph views: Ship vs flight vs destination

5. **Geographic Map** (Leaflet + Mapbox GL)
   - Base layer: World map
   - Markers: Ship location (update real-time), case locations (country-level)
   - Heatmap: Density of cases by country
   - Flight routes: Animated arrows showing evacuation paths
   - Basemap: Satellite or terrain option

6. **Model Diagnostics Dashboard** (Plotly + custom)
   - Grid of 4 plots:
     - Trace plot: Parameter samples over HMC iterations (show convergence)
     - Autocorrelation: ESS calculation visualization
     - Pair plot: Posterior correlations (2D scatter matrix)
     - Rank plot: Rank uniformity (diagnostic for sampling issues)

---

### FR-5.4: Mobile Responsiveness
**Requirement**: Dashboard SHALL be usable on tablets (primary) and smartphones (secondary).

**Breakpoints**:
- **Desktop** (≥1200px): Full dashboard, all charts visible
- **Tablet** (768–1199px): Single-column layout, charts stack vertically
- **Mobile** (≤767px): Minimalist view, key metrics cards only

**Mobile-Optimized Views**:
- Situation Report: Large case count cards, collapsed charts
- Scenario Explorer: Horizontal swipe between scenarios
- Data Quality: Searchable case list (no table)
- Charts: Touch-enabled zoom/pan

---

## FR-6: SECURITY & COMPLIANCE

### FR-6.1: Authentication & Authorization
**Requirement**: System SHALL enforce role-based access control with audit logging.

**Roles**:
1. **Public Health Epidemiologist**: Read forecasts, run scenarios, download PDFs
2. **Data Manager**: Manage case database, validate/correct data, trigger refits
3. **System Administrator**: Manage users, configure data sources, view logs
4. **Read-Only Viewer**: View dashboards only (for non-technical stakeholders)

**Authentication**:
- OAuth 2.0 / OpenID Connect (integrate with government identity providers)
- API keys for programmatic access (for CI/CD pipelines, external partners)
- 2FA for admins (TOTP or hardware keys)

**Authorization**:
- Role-based access control (RBAC) via attribute-based policies
- Example: "Only epidemiologists from WHO + UK HSA can trigger manual refits"
- Audit log: Every action (login, data view, parameter change, export) recorded

---

### FR-6.2: Data Privacy & HIPAA Compliance
**Requirement**: System SHALL protect patient privacy and meet HIPAA/GDPR requirements.

**De-identification**:
- Patient names/identifiers removed before ingestion
- Replaced with anonymized IDs (e.g., "anon_p0042")
- Geolocation: Country-level only (not hospital/clinic name)
- Dates: Keep for epidemiological analysis (necessary)

**Data Retention**:
- Patient records: Retain for 7 years post-case closure (HIPAA requirement)
- Audit logs: Retain for 3 years
- Posterior samples: Archive indefinitely (for scientific reproducibility)

**Access Controls**:
- Encryption at rest: AES-256 (PostgreSQL + S3)
- Encryption in transit: TLS 1.3
- Database access: Only authorized services (no direct human access)
- S3 buckets: Private, versioning enabled, MFA delete required

**Data Breach Procedures**:
- If unauthorized access detected:
  - Immediate notification to health authorities (within 24h)
  - Audit log review to identify affected records
  - Credential rotation (API keys, passwords)
  - Public disclosure (if required by law)

---

### FR-6.3: Audit & Compliance Reporting
**Requirement**: System SHALL generate compliance reports for regulatory bodies.

**Audit Trail**:
- Every data modification: WHO modified case X at timestamp T by user U for reason R
- Every forecast generation: Model version V ran scenario S at timestamp T with parameters P
- Every inference: Triggered by event E, completed with diagnostics D, divergences N

**Compliance Reports** (Auto-generated, monthly):
- Data integrity report: Number of invalid records detected, correction rate
- Model stability report: Parameter drift, hindcast accuracy, divergence counts
- Security report: Access violations, unauthorized API calls, failed auth attempts
- Performance report: API latency percentiles, uptime %, false-alert rate

---

## FR-7: DEPLOYMENT & OPERATIONS

### FR-7.1: Infrastructure & Hosting
**Requirement**: System SHALL be deployable on-premise or cloud with minimal configuration.

**Deployment Options**:

**Option A: AWS Cloud (Recommended for scalability)**
- RDS PostgreSQL + TimescaleDB extension
- S3 for posterior traces, forecasts, logs
- Lambda for inference service (ephemeral, auto-scaling)
- API Gateway + ALB for load balancing
- CloudWatch for monitoring
- Cost: ~$5k–15k/month depending on scale

**Option B: On-Premise (Kubernetes)**
- Self-hosted PostgreSQL + TimescaleDB
- NFS for data persistence
- Kubernetes cluster (3+ nodes) for service orchestration
- Prometheus + Grafana for monitoring
- Cost: Infrastructure + 1 FTE sysadmin

**Option C: Hybrid (Recommended for regulated environments)**
- On-prem PostgreSQL (sensitive case data)
- Cloud inference service (AWS Lambda) with VPN tunnel
- Data encrypted in transit (TLS)

---

### FR-7.2: Monitoring & Alerting
**Requirement**: System SHALL monitor performance and alert operators to failures.

**Metrics to Monitor** (Prometheus):
- API response time (p50, p95, p99)
- Database query latency
- Cache hit rate (Redis)
- Inference job duration, divergence count
- Message queue depth (Kafka consumer lag)
- Disk usage, memory utilization

**Alerts** (PagerDuty integration):
- Inference job > 1 hour without completion → Page oncall
- Model convergence failure (Rhat > 1.05) → Email epidemiologists
- API error rate > 1% → Page backend team
- Database disk > 85% → Page sysadmin
- PICP < 0.85 (model fit degraded) → Email data science team

**Logging** (ELK Stack or CloudWatch):
- Structured logs: JSON with request ID, user, action
- Log retention: 90 days hot, 1 year archive
- Searchable: Via timestamp, user, case ID, error type

---

### FR-7.3: Backup & Disaster Recovery
**Requirement**: System SHALL recover from data loss or service failure within 1 hour.

**Backup Strategy**:
- PostgreSQL: Daily snapshots (RDS automated backups) + point-in-time recovery (35-day window)
- S3: Versioning enabled on all buckets; cross-region replication
- Redis cache: Non-critical (can be regenerated); no backup required
- Configuration: Version control (GitHub) with secrets management (AWS Secrets Manager)

**Recovery Procedures**:
- **Case data loss**: Restore latest RDS snapshot (max 1-hour data loss)
- **Forecast cache loss**: Re-run simulations (60 sec, users see "Computing..." message)
- **Service outage**: Switch to read-only replica (if multi-region deployed)
- **Total failure**: Manual restoration from S3 + RDS backups (estimated 1–2 hours)

**Testing**: Monthly disaster recovery drill (restore to staging environment)

---

### FR-7.4: Continuous Deployment & Testing
**Requirement**: System SHALL support frequent updates with automated testing.

**CI/CD Pipeline** (GitHub Actions):
1. **On PR commit**:
   - Unit tests (Python pytest + JavaScript Jest)
   - Code quality checks (pylint, Black formatter)
   - Type checking (mypy, TypeScript)
   - Security scanning (SAST: Bandit, npm audit)

2. **On merge to main**:
   - Integration tests (Docker Compose stack)
   - API contract tests (pytest + OpenAPI validation)
   - Database migration tests (test rollback + rollforward)

3. **On tag (release)**:
   - Build Docker images
   - Push to container registry (ECR)
   - Deploy to staging environment
   - Run smoke tests (health checks, basic forecasts)
   - Await manual approval
   - Deploy to production (blue-green deployment, 5-min rollback window)

**Version Control**:
- Inference code: Tagged, reproducible builds (Docker)
- Data: DVC (Data Version Control) for posterior traces, contact networks
- Infrastructure: Terraform for IaC (Infrastructure as Code)

---

# ARCHITECTURE OVERVIEW

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          FRONTEND TIER                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  React Dashboard                                                     │
│  ├── Situation Report (timeseries charts, key metrics)             │
│  ├── Scenario Explorer (interactive comparisons)                    │
│  ├── Model Validation (diagnostics, hindcast accuracy)             │
│  ├── Data Management (case list, quality alerts) [Admin]           │
│                                                                       │
│  WebSocket Client (Socket.io) ──→ Real-time updates                │
│                                                                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS / WebSocket
┌──────────────────────────────▼──────────────────────────────────────┐
│                         API TIER (FastAPI)                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  /api/v1/cases                      [GET/POST case data]           │
│  /api/v1/forecasts/{scenario}       [GET forecasts]                │
│  /api/v1/inference/{version}        [GET inference results]        │
│  /api/v1/validation/*               [GET model diagnostics]        │
│  /api/v1/scenarios/compare          [GET scenario comparison]      │
│  /api/v1/admin/*                    [Auth-protected admin ops]     │
│                                                                       │
│  ├── Request validation (Pydantic schemas)                         │
│  ├── Authentication (OAuth 2.0, JWT)                               │
│  ├── Rate limiting                                                  │
│  ├── Response caching (Redis)                                      │
│  ├── Error handling + structured logging                           │
│                                                                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ Data Ingest  │      │  Inference   │      │ Simulation   │
│   Service    │      │   Service    │      │   Service    │
├──────────────┤      ├──────────────┤      ├──────────────┤
│              │      │              │      │              │
│ ┌─────────┐ │      │ ┌──────────┐ │      │ ┌──────────┐ │
│ │ WHO RSS │ │      │ │ PyMC3    │ │      │ │  Mesa    │ │
│ │ Fetcher │ │      │ │ Inference│ │      │ │   ABM    │ │
│ └─────────┘ │      │ └──────────┘ │      │ └──────────┘ │
│             │      │              │      │              │
│ ┌─────────┐ │      │ ┌──────────┐ │      │ ┌──────────┐ │
│ │ Google  │ │      │ │ Posterior│ │      │ │ Contact  │ │
│ │ Forms   │ │      │ │ Sampling │ │      │ │ Network  │ │
│ │ Webhook │ │      │ └──────────┘ │      │ │ Simulator│ │
│ └─────────┘ │      │              │      │ └──────────┘ │
│             │      │              │      │              │
│ ┌─────────┐ │      │              │      │              │
│ │Validator│ │      │              │      │ ┌──────────┐ │
│ │ + QA    │ │      │              │      │ │ Monte    │ │
│ └─────────┘ │      │              │      │ │ Carlo    │ │
│             │      │              │      │ │ Ensemble │ │
└──────────────┘      └──────────────┘      │ └──────────┘ │
                                             │              │
                                             └──────────────┘
        │                      │                      │
        └──────────────────────┼──────────────────────┘
                               │
        ┌──────────────────────┴──────────────────────┐
        │                                             │
        ▼                                             ▼
┌──────────────────────────────────┐      ┌─────────────────────┐
│     DATA PERSISTENCE TIER        │      │   MESSAGE QUEUE     │
├──────────────────────────────────┤      ├─────────────────────┤
│                                  │      │                     │
│ PostgreSQL + TimescaleDB         │      │ Apache Kafka / SQS  │
│ ├── case_records                 │      │ ├── ingest events   │
│ ├── case_records_history         │      │ ├── inference jobs  │
│ ├── inference_traces             │      │ ├── alerts          │
│ ├── validation_results           │      │                     │
│ ├── forecast_results             │      │ (30-day retention)  │
│ └── user_actions (audit log)     │      │                     │
│                                  │      └─────────────────────┘
│ Redis (Cache)                    │
│ ├── forecasts (12h TTL)          │
│ ├── API responses (5m TTL)       │
│ ├── session tokens               │
│                                  │
│ S3 (File Storage)                │
│ ├── posterior traces (.nc)       │
│ ├── contact networks (.json)     │
│ ├── forecast archives            │
│ ├── logs (CloudWatch)            │
│                                  │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│    MONITORING & ALERTING         │
├──────────────────────────────────┤
│                                  │
│ Prometheus (metrics)             │
│ Grafana (dashboards)             │
│ Alert Manager → PagerDuty/Slack  │
│ ELK (logs)                       │
│                                  │
└──────────────────────────────────┘
```

---

## Data Flow Diagrams

### Flow 1: New Case Ingestion & Inference Trigger

```
WHO releases updated case count (6-hourly feed)
        ↓
Data Ingest Service (polls RSS)
        ↓
Parse case: symptom_onset=2026-05-07, country=ZA, confirmed=True
        ↓
Kafka queue: {"source": "WHO", "case_data": {...}}
        ↓
Validation Service (consumes message)
    ├── Temporal consistency check ✓
    ├── Geographic plausibility ✓
    ├── Duplicate detection ✓
    └── Quality score = 0.88 (PASS)
        ↓
PostgreSQL: INSERT into case_records + case_records_history
        ↓
Check trigger condition:
    - Is this the first case today? OR
    - Is validation_score ≥ 0.80? OR
    - Manual request from epidemiologist?
        ↓ YES
Inference Service (triggered by event)
        ↓
Fetch all validated cases from PostgreSQL (8 cases)
        ↓
Construct likelihood from case timeline + contact network
        ↓
PyMC3: MCMC sampling (2000 draws, 1000 warmup)
        ↓
Diagnostics check:
    - Rhat < 1.01 ✓
    - Divergences < 50 ✓
    - Convergence PASS
        ↓
Save posterior: s3://eosp-data/posteriors/v1.4.3.nc
        ↓
Simulation Service (triggered)
        ↓
Run 10,000 ABM trajectories (parallel, 16 cores, 60 sec)
        ↓
Generate forecast JSONs (baseline + 4 scenarios)
        ↓
Cache in Redis: forecast:v1.4.3:baseline (TTL 12h)
        ↓
Publish WebSocket event:
    {
        "type": "FORECAST_UPDATED",
        "model_version": "v1.4.3",
        "scenario": "baseline",
        "median_cases_day_14": 15
    }
        ↓
React dashboard (subscribed to WebSocket) animates chart update
        ↓
Email alert to epidemiologists:
    "Model refitted with new case data. See dashboard for updated forecast."
        ↓
END (elapsed time: ~30 minutes from WHO publish to dashboard update)
```

### Flow 2: Epidemiologist Requests Scenario Analysis

```
User clicks "Scenario Explorer" tab
        ↓
Selects: "Baseline vs Evacuation Delay +7 Days"
        ↓
React frontend calls:
    GET /api/v1/scenarios/compare?scenarios=baseline,evacuation_delay_7d
        ↓
API FastAPI endpoint:
    1. Check cache key: "scenario_compare:baseline,evacuation_delay_7d"
    2. Cache hit? Return cached response
    3. Cache miss? Proceed to simulation
        ↓ (assuming cache miss)
API calls Simulation Service:
    POST /internal/simulate
    {
        "scenarios": ["baseline", "evacuation_delay_7d"],
        "model_version": "v1.4.3"
    }
        ↓
Simulation Service:
    1. Load posterior from S3: v1.4.3.nc
    2. For each scenario:
       a. Construct contact network (network_modifications applied)
       b. Run 10,000 ABM trajectories with parameter variation
       c. Aggregate percentiles
    3. Return comparison JSON
        ↓
API caches response (TTL 1 hour)
        ↓
API returns JSON to React:
    {
        "baseline": {
            "cases_day_14": {"median": 13, "ci_95": [7, 22]},
            "deaths_day_14": {"median": 5, "ci_95": [2, 9]}
        },
        "evacuation_delay_7d": {
            "cases_day_14": {"median": 20, "ci_95": [12, 30]},
            "deaths_day_14": {"median": 8, "ci_95": [4, 13]},
            "vs_baseline": {"case_increase_pct": 54}
        }
    }
        ↓
React renders comparison:
    - 3-panel layout with charts
    - Summary: "Delaying evacuation by 7 days increases cases by 54% (95% CI: 20–86%)"
        ↓
User exports PDF via button
        ↓
API generates PDF (via Plotly export + Weasyprint template)
        ↓
User downloads "scenario_comparison_2026-05-07.pdf"
        ↓
END
```

---

# DATA MODEL & PERSISTENCE

## Database Schema (PostgreSQL + TimescaleDB)

```sql
-- Core tables

TABLE case_records (
    case_id UUID PRIMARY KEY,
    patient_identifier VARCHAR(255),  -- Anonymized
    symptom_onset_date DATE NOT NULL,
    hospitalization_date DATE,
    death_date DATE,
    location_country VARCHAR(2),  -- ISO-3166-1 alpha-2
    location_airport_code VARCHAR(3),  -- IATA code
    location_city VARCHAR(255),
    confirmed_or_suspected ENUM('confirmed', 'suspected'),
    lab_test_result ENUM('PCR_positive', 'PCR_negative', 'serology_positive', 'not_tested'),
    symptoms_text TEXT,  -- Free text: fever, diarrhea, respiratory distress, etc.
    contacts JSON,  -- Array of contact objects (anonymized IDs)
    data_source VARCHAR(100),  -- WHO_DON, Manual_Form, Contact_Trace_DB
    ingestion_timestamp TIMESTAMP DEFAULT NOW(),
    validation_score FLOAT CHECK (validation_score BETWEEN 0 AND 1),
    version INTEGER DEFAULT 1,
    updated_by VARCHAR(255),  -- User/system identifier
    updated_reason VARCHAR(255),  -- New_case, Lab_confirmed, Correction
    active BOOLEAN DEFAULT TRUE,  -- Soft delete
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

TABLE case_records_history (
    history_id SERIAL PRIMARY KEY,
    case_id UUID REFERENCES case_records(case_id),
    case_data_json JSONB,  -- Full snapshot of previous state
    timestamp TIMESTAMP DEFAULT NOW(),
    source VARCHAR(100),
    validation_score FLOAT,
    change_reason VARCHAR(255),
    changed_by VARCHAR(255),
    parent_version_id INTEGER
);

TABLE validation_results (
    validation_id UUID PRIMARY KEY,
    case_id UUID REFERENCES case_records(case_id),
    validation_timestamp TIMESTAMP DEFAULT NOW(),
    checks_passed JSONB,  -- e.g., {"temporal_consistency": true, "duplicate_detection": false}
    issues TEXT[],  -- Array of issue descriptions
    quality_score FLOAT,
    quarantine_status ENUM('approved', 'quarantined', 'requires_review')
);

TABLE inference_traces (
    trace_id UUID PRIMARY KEY,
    version VARCHAR(50) UNIQUE,  -- e.g., "v1.4.3-20260507T143200Z"
    timestamp TIMESTAMP DEFAULT NOW(),
    trigger_type ENUM('new_case', 'cron', 'anomaly', 'manual'),
    n_cases INTEGER,
    n_draws INTEGER DEFAULT 2000,
    n_warmup INTEGER DEFAULT 1000,
    posterior_s3_path VARCHAR(500),  -- Path to .nc file
    posterior_summary JSONB,  -- {p_transmit: {mean, std, ci_95}, ...}
    diagnostics JSONB,  -- {rhat: {...}, divergences: 23, ...}
    execution_time_seconds FLOAT,
    data_quality_mean FLOAT,  -- Mean validation_score of included cases
    convergence_status ENUM('converged', 'warning', 'failed'),
    created_at TIMESTAMP DEFAULT NOW()
);

TABLE forecast_results (
    forecast_id UUID PRIMARY KEY,
    model_version VARCHAR(50) REFERENCES inference_traces(version),
    scenario VARCHAR(100),  -- baseline, quarantine_immediate, etc.
    generated_timestamp TIMESTAMP DEFAULT NOW(),
    forecast_json JSONB,  -- Full timeseries data (see FR-3.4)
    n_simulations INTEGER DEFAULT 10000,
    execution_time_seconds FLOAT,
    s3_archive_path VARCHAR(500)  -- Full ensemble archived to S3
);

TABLE contact_networks (
    network_id UUID PRIMARY KEY,
    name VARCHAR(255),  -- "Ship Network", "Flight JNB-AMS", etc.
    network_type ENUM('ship', 'flight', 'destination', 'custom'),
    nodes_json JSONB,  -- Array of node objects: {id, type, status}
    edges_json JSONB,  -- Array of edge objects: {source, target, type, weight}
    temporal_window_start DATE,
    temporal_window_end DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Hypertable for timeseries (TimescaleDB)
CREATE TABLE timeseries_forecast (
    time TIMESTAMP NOT NULL,
    model_version VARCHAR(50),
    scenario VARCHAR(100),
    metric VARCHAR(100),  -- cumulative_cases, deaths, new_cases_daily
    value FLOAT,
    ci_lower FLOAT,
    ci_upper FLOAT
);

SELECT create_hypertable('timeseries_forecast', 'time', if_not_exists => TRUE);

-- Audit table
TABLE user_actions (
    action_id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT NOW(),
    user_id VARCHAR(255),
    action_type VARCHAR(100),  -- view_case, edit_case, trigger_refit, export_forecast
    resource_type VARCHAR(100),  -- case, forecast, model
    resource_id VARCHAR(255),
    details JSONB,
    ip_address INET
);

-- Indexes for performance
CREATE INDEX idx_case_symptom_date ON case_records(symptom_onset_date);
CREATE INDEX idx_case_country ON case_records(location_country);
CREATE INDEX idx_case_validation ON case_records(validation_score);
CREATE INDEX idx_trace_version ON inference_traces(version);
CREATE INDEX idx_forecast_model ON forecast_results(model_version, scenario);
CREATE INDEX idx_timeseries ON timeseries_forecast (time DESC, model_version, scenario);
```

---

# INTEGRATION & EXTERNAL SYSTEMS

## Data Source Integrations

### WHO Disease Outbreak News (RSS)
- **URL**: https://www.who.int/feeds/entity/coe/en/feed.xml
- **Polling**: Every 6 hours
- **Data extraction**: Parse HTML summary → extract case count, dates, locations (regex)
- **Challenge**: Unstructured text → implement NLP-based extraction (BeautifulSoup + regex patterns)
- **Confidence weight**: 0.95 (official source)

### Google Forms (Case Submissions)
- **Purpose**: Epidemiologists manually report new cases/contacts
- **Integration**: Zapier webhook → HTTP POST to /api/v1/cases
- **Form fields**: 
  - Symptom onset date
  - Location (country, city, airport)
  - Lab test result (confirm/suspect)
  - Close contacts (comma-separated names → anonymize locally)
- **Confidence weight**: 0.85 (manual, but from trained epidemiologists)

### National Health Authority APIs (Contact Tracing)
- **Systems**: PRIME (UK), EDR (South Africa), national COVID-19 databases
- **Integration**: Authenticate via API key (stored in AWS Secrets Manager)
- **Endpoint**: `POST /api/v1/cases` with authorization header
- **Payload**: Standardized JSON schema (draft with health authorities)
- **Confidence weight**: 0.90 (authoritative sources)

### Flight Tracking (OpenSky Network)
- **Purpose**: Obtain passenger manifests + route information (for evacuation modeling)
- **API**: https://opensky-network.org/api/flights/all
- **Queries**: Flight history by callsign/tail number
- **Challenge**: No passenger-level data (privacy); use seat capacity + density estimates
- **Confidence weight**: 0.75 (indirect proxy)

### News Monitoring (Optional, Low Priority)
- **Purpose**: Detect outbreak mentions for situational awareness (not for inference)
- **Integration**: GDELT project or NewsAPI
- **Usage**: Alerts to epidemiologists ("new reports of respiratory illness in X country")
- **Confidence weight**: 0.30 (unreliable, contradictory reports)

---

## External APIs Consumed

| API | Purpose | Rate Limit | Fallback |
|-----|---------|-----------|----------|
| Google Geocoding | Resolve city/airport names to coordinates | 50k/day | Cache results |
| OpenSky Network | Flight routes, cabin capacity | 400 calls/day | Use static schedules |
| Maps/Nominatim | Geographic visualization | 1 req/sec | Mapbox tiles |
| PagerDuty | Alert escalation | N/A | Email fallback |

---

# UPDATE STRATEGY & DATA PIPELINE

## Update Triggers & Schedules

| Trigger | Frequency | Latency | Parameters | Notes |
|---------|-----------|---------|-----------|-------|
| **WHO Feed** | Every 6h | ~5 min | Ingestion only | Automated RSS polling |
| **New Case (High Quality)** | Immediate | ~30 min | Full refit | Conditional: score ≥0.80 |
| **Batch Refit** | Every 12h | ~1 hour | Full refit + simulation | Scheduled: 06:00 + 18:00 UTC |
| **Anomaly Detection** | Every 30 min | ~15 min | Full refit | If observed > 3σ forecast |
| **Manual Request** | On-demand | ~30 min | Full refit | Epidemiologist-initiated |
| **Rollback** | On-demand | <5 min | Load old model | Revert to previous version |

---

## Version Management Strategy

**Semantic Versioning**: `vMAJOR.MINOR.PATCH-TIMESTAMP`

- **MAJOR**: Fundamental model change (new parameters, data sources)
- **MINOR**: Parameter re-estimation with new cases
- **PATCH**: Bug fixes, configuration updates
- **TIMESTAMP**: ISO-8601 UTC of generation

**Example**:
- `v1.0.0-20260506T000000Z`: Initial model
- `v1.0.1-20260506T120000Z`: Bug fix in contact network
- `v1.1.0-20260507T143200Z`: New cases (minor update)
- `v2.0.0-20260510T000000Z`: Added antivirals as parameter (major)

**Retention Policy**:
- Keep last 30 versions in production
- Archive older versions to S3
- Never delete (all versions auditable)

---

# SUCCESS METRICS & KPIs

## Model Validation Metrics

| Metric | Target | Rationale |
|--------|--------|-----------|
| **Hindcast Accuracy (PICP)** | 0.90–0.98 | 95% CI should cover ~95% of actual cases |
| **Prediction Interval Width** | <50% of median | Not overconfident; reflects true uncertainty |
| **Convergence (Rhat)** | <1.01 | Parameters have converged to true posterior |
| **Divergences** | <50 per 2000 draws | Hamiltonian sampler is stable |
| **Effective Sample Size** | >400 per parameter | Sufficient samples for credible intervals |

## Operational Metrics

| Metric | Target | Rationale |
|--------|--------|-----------|
| **New Case → Forecast Latency** | <30 min | Decision windows are short |
| **API Response Time** | <500ms p95 | Dashboard responsiveness |
| **Forecast Cache Hit Rate** | >80% | Most users requesting baseline scenarios |
| **Uptime (SLA)** | 99.5% | <4 hours downtime/month |
| **Data Validation Pass Rate** | >95% | Minimize manual review overhead |

## User Engagement Metrics

| Metric | Target | Rationale |
|--------|--------|-----------|
| **Daily Active Users** | 20+ | Adoption across health authorities |
| **Forecast Download Rate** | 50+ exports/week | Used in briefings + reports |
| **Scenario Requests** | 30+ per week | Impact of decision-making |
| **Admin Corrections** | <5 per 100 cases | Low error rate in data |

---

# RISK ASSESSMENT & MITIGATION

## Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| **Model divergence** (sampler fails) | Medium | High | 1. Robust priors (weakly informative). 2. Restart with tuned step size. 3. Fallback: previous posterior |
| **Contact network data unavailable** | Medium | Medium | Use default network (ship roster + flight manifests). Sensitivity test with network uncertainty |
| **Posterior overfitting** (learns noise) | Low | Medium | Cross-validation, posterior predictive checks. Monitor PICP |
| **API latency spikes** | Low | Medium | Cache aggressively. Auto-scale API service. Graceful degradation (return cached forecast) |
| **Data breach (case records leaked)** | Low | Critical | Encryption at rest/transit. RBAC + audit logs. Incident response plan tested quarterly |

## Operational Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| **Key person dependency** (data scientist) | Medium | High | Document inference methodology. Open-source code. Cross-train team member |
| **Health authority data API breaks** | Medium | Medium | Fallback to Google Forms manual entry. Cache last-received data |
| **Forecast becomes inaccurate (new disease mechanism)** | Low | Critical | Monitor hindcast accuracy daily. Alert if PICP < 0.85. Escalate to epidemiologist review |
| **Reputational damage** (over-confident forecast wrong) | Low | Critical | Conservative CI (wider, not narrower). Transparency: publish all assumptions + code. Public validation reports |

## Domain Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| **Andes virus mutates** (transmission increases) | Very Low | Critical | Model monitoring. If new cases >> forecast, trigger auto refit + epidemiologist alert |
| **Second outbreak source** (rodent exposure on shore) | Low | High | Monitor for geographic clustering. Scenario: "multi-source outbreak." Allow manual network edits |
| **Vaccine/antiviral becomes available** | Medium | Medium | Implement scenario: "Antivirals distributed to contacts" (reduces CFR, not transmission) |

---

# ROLLOUT PLAN

## Phase 1: Development & Validation (Weeks 1–6)

**Week 1**:
- Build data ingestion pipeline (Kafka + WHO RSS feed)
- Implement case validator + PostgreSQL schema
- Set up CI/CD (GitHub Actions)

**Week 2–3**:
- Implement Bayesian inference (PyMC3 skeleton)
- Validate against known cases (first 3 cases)
- Parameter sensitivity analysis

**Week 4–5**:
- Build ABM simulation (Mesa framework)
- Implement scenario parameterization
- Generate forecasts (all 8 known cases)

**Week 6**:
- Implement FastAPI endpoints
- Build React dashboard (basic charts)
- End-to-end integration test

**Success Criteria**:
- Model can retrodict 8 observed cases with 95% CI coverage
- Inference converges (Rhat <1.01)
- API latency <500ms
- Dashboard loads in <2s

---

## Phase 2: Closed Beta (Weeks 7–8)

**Users**: WHO coordinator (Dr. Van Kerkhove), UK HSA epidemiologist, data scientist peer review

**Activities**:
- Daily production runs (ingest real cases, refit)
- Collect feedback (what scenarios matter most?)
- Fix bugs + improve hindcast accuracy
- Document API for external access

**Deliverables**:
- Refined dashboard with epidemiologist feedback
- API documentation (OpenAPI 3.0)
- Methodology paper draft (for publication)

**Success Criteria**:
- 0 critical bugs in 2-week period
- Epidemiologists confident in forecasts
- Peer reviewers approve methodology

---

## Phase 3: Public Launch (Week 9)

**Users**: All health authorities + WHO

**Activities**:
- Announce publicly (press release)
- Open data access (real-time case list, forecasts)
- Enable GitHub issues (community feedback)
- Train health authority operators

**Communications**:
- Blog post: "Open-source outbreak forecasting for global coordination"
- Webinar: Methodology + how to use dashboard
- FAQ + troubleshooting guide

**Launch Criteria**:
- 99.5% API uptime in staging (Week 8)
- Disaster recovery tested + working
- Security audit completed (no high-risk findings)
- Data governance agreed by health authorities

---

## Phase 4: Ongoing Operations (Week 10+)

**Maintenance**:
- Daily inference runs (automated)
- Weekly validation reports (hindcast accuracy)
- Monthly security audits
- Quarterly disaster recovery drills

**Improvements**:
- Add new data sources (as health authorities onboard)
- Implement user-requested scenarios
- Publish scientific papers (methodology validation)
- Explore advanced models (spatial transmission, variants)

---

# IMPLEMENTATION PRIORITIES (Scope Phasing)

## Must Have (MVP)
1. Data ingestion (WHO RSS, Google Forms)
2. Case validation + PostgreSQL persistence
3. Bayesian inference (PyMC3, basic priors)
4. Contact network modeling (ship + flights)
5. ABM simulation (Mesa, 10k trajectories)
6. API endpoints (cases, forecasts, inference)
7. Dashboard (React, timeseries charts, scenarios)
8. WebSocket updates (real-time)

## Should Have (High Priority)
1. Model validation (hindcast accuracy, PICP)
2. Admin panel (data curation, alerts)
3. Scenario comparison (side-by-side)
4. Parameter visualization (posteriors)
5. Audit logging (full lineage)
6. Monitoring (Prometheus + Grafana)

## Nice to Have (Lower Priority)
1. Contact network graph visualization (D3.js force-directed)
2. Geographic map (Mapbox)
3. Custom scenario builder (drag-drop)
4. Export to various formats (PDF, CSV, GeoJSON)
5. Multi-language dashboard (internationalization)
6. Mobile app (native iOS/Android)

---

# CONCLUSION & DEPLOYMENT READINESS

This PRD defines a **production-ready epidemiological forecasting platform** suitable for real-time outbreak management. The system balances:

- **Scientific rigor**: Bayesian inference, model validation, uncertainty quantification
- **Operational simplicity**: Automated pipelines, minimal manual intervention
- **Regulatory compliance**: HIPAA/GDPR, audit trails, data governance
- **Scalability**: Microservices, containerization, cloud-native design

**Key Design Decisions Justified**:
1. **Bayesian inference over ML**: Interpretability + uncertainty quantification matter for policy decisions
2. **ABM over compartmental models**: Heterogeneous contacts (ship, flights, hospital) require agent-level detail
3. **PostgreSQL + S3 persistence**: Immutable audit trail essential for scientific credibility
4. **FastAPI + React**: Modern stack, good for data science + rapid iteration
5. **Kubernetes deployment**: Scales inference load; on-prem option for data sovereignty

**Agentic Software Builder Next Steps**:
1. Decompose this PRD into 40–50 GitHub issues (one per feature)
2. Estimate effort (story points) for each issue
3. Create project board (MVP, Phase 2, Phase 3 columns)
4. Assign tech leads (backend, frontend, data science, devops)
5. Begin implementation (parallel where feasible)
6. Weekly sync: demo features, unblock dependencies

**Expected Timeline**: 10–12 weeks to production launch (Phases 1–3)

---

# APPENDIX A: GLOSSARY

| Term | Definition |
|------|-----------|
| **Andes virus** | Hantavirus strain with documented human-to-human transmission (35–50% CFR) |
| **ABM** | Agent-based model; simulates individual-level disease transmission |
| **Bayesian inference** | Statistical method to estimate parameters given observed data |
| **Credible interval** | Bayesian equivalent of confidence interval; 95% CI contains true parameter with 95% probability |
| **PICP** | Prediction interval coverage probability; % of observations falling within forecast CI |
| **Posterior** | Probability distribution of parameters after observing data |
| **Prior** | Initial belief about parameters (before data) |
| **R₀** | Basic reproduction number; average secondary cases per infectious person |
| **Rhat** | Convergence diagnostic; Rhat <1.01 indicates convergence |

---

**Document Version**: 1.0  
**Last Updated**: May 7, 2026  
**Author**: AI Systems Strategist  
**Status**: Ready for Agentic Builder Implementation