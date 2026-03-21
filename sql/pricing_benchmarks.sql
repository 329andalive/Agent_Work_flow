-- ============================================================
-- PRICING BENCHMARKS & TRADE VERTICALS
-- Run in Supabase SQL Editor — safe to run multiple times
-- ============================================================

-- Trade verticals registry
CREATE TABLE IF NOT EXISTS trade_verticals (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vertical_key    text UNIQUE NOT NULL,
  vertical_label  text NOT NULL,
  icon            text,
  sort_order      integer DEFAULT 0,
  active          boolean DEFAULT true,
  specialties     text[],
  created_at      timestamptz DEFAULT now()
);

-- Pricing benchmarks — researched service pricing by vertical
CREATE TABLE IF NOT EXISTS pricing_benchmarks (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vertical_key    text NOT NULL,
  vertical_label  text NOT NULL,
  service_name    text NOT NULL,
  price_low       numeric(10,2),
  price_typical   numeric(10,2),
  price_high      numeric(10,2),
  price_unit      text DEFAULT 'per job',
  sort_order      integer DEFAULT 0,
  notes           text,
  region          text DEFAULT 'northeast_us',
  active          boolean DEFAULT true,
  created_at      timestamptz DEFAULT now(),
  updated_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_benchmarks_vertical
  ON pricing_benchmarks (vertical_key, region, active);

-- Price adjustment logging (learning foundation)
CREATE TABLE IF NOT EXISTS pricing_adjustments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id       uuid REFERENCES clients(id),
  vertical_key    text,
  service_name    text,
  original_price  numeric(10,2),
  adjusted_price  numeric(10,2),
  delta           numeric(10,2),
  direction       text,
  context         text,
  created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_price_adj_client
  ON pricing_adjustments (client_id, vertical_key, service_name);

-- ============================================================
-- SEED: Trade verticals
-- ============================================================

INSERT INTO trade_verticals
  (vertical_key, vertical_label, icon, sort_order, specialties)
VALUES
  ('septic', 'Septic & Sewer', '🚽', 1,
   ARRAY['Pump-outs','Inspections','Repairs','New Installations','Risers','Baffle Replacement','Effluent Filters']),
  ('plumbing', 'Plumbing', '🔧', 2,
   ARRAY['Repairs','New Construction','Water Heaters','Fixtures','Drain Cleaning','Emergency','Remodels']),
  ('hvac', 'HVAC', '❄️', 3,
   ARRAY['AC Service','Heating Service','New Installs','Duct Work','Heat Pumps','Emergency','Tune-ups']),
  ('electrical', 'Electrical', '⚡', 4,
   ARRAY['Repairs','Panel Upgrades','New Construction','EV Chargers','Generators','Lighting','Emergency']),
  ('excavation', 'Excavation', '🚜', 5,
   ARRAY['Site Prep','Septic Systems','Foundations','Driveways','Utility Trenches','Land Clearing','Grading']),
  ('drain', 'Drain Cleaning', '🌊', 6,
   ARRAY['Drain Cleaning','Camera Inspection','Hydro Jetting','Root Removal','Emergency','Sewer Line']),
  ('general', 'General Contracting', '🔨', 7,
   ARRAY['Repairs','Remodeling','Decks','Framing','Roofing','Siding','Windows','Insulation']),
  ('landscaping', 'Landscaping', '🌿', 8,
   ARRAY['Mowing','Planting','Mulching','Hardscaping','Irrigation','Tree Work','Cleanup','Design']),
  ('property_mgmt', 'Property Maintenance', '🏠', 9,
   ARRAY['General Maintenance','Seasonal Prep','Snow Removal','Gutter Cleaning','Pressure Washing','Inspections','Handyman','Tenant Turnover'])
ON CONFLICT (vertical_key) DO NOTHING;

-- ============================================================
-- SEED: Pricing benchmarks — Northeast US rural market
-- Sources: HomeAdvisor, Angi, Thumbtack, Fixr, state trade
-- associations, contractor forums. Cross-referenced 2024-2025.
-- Prices reflect rural New England (ME, NH, VT, rural MA/CT).
-- ============================================================

-- SEPTIC (15 services) — Confidence: HIGH
-- Sources: Maine DEP, HomeAdvisor, NH septic associations
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('septic','Septic & Sewer','Pump-out — 1,000 gal tank',225,275,350,'per job',1,'Standard residential. Price varies by access and distance.'),
('septic','Septic & Sewer','Pump-out — 1,500 gal tank',275,325,400,'per job',2,'Most common residential size in New England.'),
('septic','Septic & Sewer','Pump-out — 2,000 gal tank',325,400,500,'per job',3,'Larger homes, multi-family. May require longer hose.'),
('septic','Septic & Sewer','Septic inspection',100,175,250,'per job',4,'Required for real estate transactions in most NE states.'),
('septic','Septic & Sewer','Baffle replacement',150,225,350,'per job',5,'Common repair. Price depends on tank access and condition.'),
('septic','Septic & Sewer','Riser installation',200,300,450,'per job',6,'Brings access to grade level. Saves on future pump-outs.'),
('septic','Septic & Sewer','Effluent filter install',75,125,200,'per job',7,'Protects drain field. Often added during pump-out.'),
('septic','Septic & Sewer','Tank locating / lid finding',75,150,250,'per job',8,'Probing and digging to locate buried components.'),
('septic','Septic & Sewer','Septic system repair — minor',300,600,1200,'per job',9,'Pipe repair, distribution box fix, minor issues.'),
('septic','Septic & Sewer','Septic system repair — major',1500,3500,7000,'per job',10,'Pump replacement, drain field repair, structural.'),
('septic','Septic & Sewer','New septic system install',8000,15000,25000,'per job',11,'Full system. Varies hugely by soil, size, and type.'),
('septic','Septic & Sewer','Drain field repair/replace',3000,6000,12000,'per job',12,'Most expensive repair. Depends on field size and soil.'),
('septic','Septic & Sewer','Grease trap pumping',150,250,400,'per job',13,'Commercial kitchens and restaurants.'),
('septic','Septic & Sewer','Camera inspection',150,250,400,'per job',14,'Sewer line camera to diagnose problems.'),
('septic','Septic & Sewer','Emergency service surcharge',100,175,250,'per job',15,'Added to base price for after-hours/weekend calls.'),
-- Travel charge
('septic','Septic & Sewer','Travel charge (per mile over 20)',2,2.50,3.50,'per mile',16,'Standard mileage for jobs outside service radius.')
ON CONFLICT DO NOTHING;

-- PLUMBING (15 services) — Confidence: HIGH
-- Sources: HomeAdvisor, Angi, Thumbtack, NH/ME plumber associations
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('plumbing','Plumbing','Service call / diagnosis',85,125,175,'per visit',1,'Trip charge to come look. Often credited toward repair.'),
('plumbing','Plumbing','Drain cleaning — standard',125,200,300,'per job',2,'Snake or auger. Main line costs more than branch.'),
('plumbing','Plumbing','Water heater replacement — tank',800,1200,1800,'per job',3,'40-50 gal tank. Includes removal of old unit.'),
('plumbing','Plumbing','Water heater replacement — tankless',1500,2500,4000,'per job',4,'Higher equipment cost. Gas line work may be needed.'),
('plumbing','Plumbing','Toilet repair',95,150,250,'per job',5,'Flapper, fill valve, wax ring. Parts + labor.'),
('plumbing','Plumbing','Toilet replacement',250,400,600,'per job',6,'Includes new toilet, wax ring, supply line.'),
('plumbing','Plumbing','Faucet replacement',125,225,350,'per job',7,'Kitchen or bath. Varies by fixture complexity.'),
('plumbing','Plumbing','Pipe repair — copper/PEX',150,300,600,'per job',8,'Per repair. Frozen pipe repair on the higher end.'),
('plumbing','Plumbing','Sewer line repair',500,1500,4000,'per job',9,'Depends on depth, access, and method (trenchless vs dig).'),
('plumbing','Plumbing','Sump pump installation',400,700,1200,'per job',10,'Includes pump, basin, and check valve.'),
('plumbing','Plumbing','Garbage disposal install',150,250,400,'per job',11,'Includes disposal unit and labor.'),
('plumbing','Plumbing','Water line repair',200,500,1500,'per job',12,'Main water line from street to house.'),
('plumbing','Plumbing','Fixture installation',75,150,300,'per fixture',13,'Sink, tub, shower valve. Labor only.'),
('plumbing','Plumbing','Emergency after-hours',175,275,400,'per visit',14,'After-hours surcharge plus regular repair cost.'),
('plumbing','Plumbing','Hourly rate',85,110,150,'per hour',15,'Standard labor rate for time-and-materials jobs.')
ON CONFLICT DO NOTHING;

-- HVAC (15 services) — Confidence: HIGH
-- Sources: HomeAdvisor, Angi, HVAC contractor forums, Energy Star
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('hvac','HVAC','AC tune-up / service',79,129,189,'per visit',1,'Annual maintenance. Includes filter, coil check, refrigerant.'),
('hvac','HVAC','Furnace tune-up / service',79,129,189,'per visit',2,'Annual heating season prep. Clean burners, check heat exchanger.'),
('hvac','HVAC','AC repair — diagnosis',85,150,225,'per visit',3,'Diagnostic fee. Often credited toward repair.'),
('hvac','HVAC','Heat pump installation — mini-split',3000,5000,8000,'per job',4,'Single zone. Multi-zone runs $8K-$15K+.'),
('hvac','HVAC','Central AC installation',3500,5500,9000,'per job',5,'Depends on home size and ductwork condition.'),
('hvac','HVAC','Furnace replacement',2500,4500,7000,'per job',6,'Gas or oil. Includes removal of old unit.'),
('hvac','HVAC','Boiler service / tune-up',150,250,400,'per visit',7,'Oil boiler cleaning, nozzle, filter, test.'),
('hvac','HVAC','Boiler replacement',4000,7000,12000,'per job',8,'Oil or gas. High-efficiency units cost more.'),
('hvac','HVAC','Duct cleaning — whole house',300,500,800,'per job',9,'Price depends on home size and duct complexity.'),
('hvac','HVAC','Duct repair / sealing',200,400,800,'per job',10,'Per section. Improves efficiency 20-30%.'),
('hvac','HVAC','Thermostat installation — smart',100,200,350,'per job',11,'Includes thermostat and wiring. Nest/Ecobee range.'),
('hvac','HVAC','Filter replacement',25,50,75,'per visit',12,'Standard filter swap during service call.'),
('hvac','HVAC','Refrigerant recharge',150,300,500,'per job',13,'R-410A. Price depends on amount needed.'),
('hvac','HVAC','Emergency service — HVAC',175,300,450,'per visit',14,'After-hours/weekend. Plus repair cost.'),
('hvac','HVAC','Hourly rate — HVAC tech',85,115,150,'per hour',15,'Standard labor for time-and-materials.')
ON CONFLICT DO NOTHING;

-- ELECTRICAL (15 services) — Confidence: HIGH
-- Sources: HomeAdvisor, Angi, NEC cost guides, state licensing boards
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('electrical','Electrical','Service call / diagnosis',85,125,175,'per visit',1,'Trip charge. May be credited toward work.'),
('electrical','Electrical','Outlet / switch replacement',75,150,225,'per job',2,'Per outlet. GFCI outlets cost more.'),
('electrical','Electrical','Panel upgrade — 100A to 200A',1500,2500,4000,'per job',3,'Required for EV chargers, additions, etc.'),
('electrical','Electrical','Whole house rewire',8000,12000,20000,'per job',4,'Older homes. Price by sq ft and access.'),
('electrical','Electrical','Lighting installation — per fixture',100,175,300,'per fixture',5,'Recessed, pendant, sconce. Depends on wiring.'),
('electrical','Electrical','Ceiling fan installation',150,250,400,'per job',6,'With existing wiring. New wiring adds $100-200.'),
('electrical','Electrical','EV charger installation — Level 2',500,900,1500,'per job',7,'240V circuit + charger mount. Panel upgrade extra.'),
('electrical','Electrical','Generator installation — portable hookup',500,1000,1800,'per job',8,'Transfer switch + inlet box + wiring.'),
('electrical','Electrical','Generator installation — whole house',4000,8000,15000,'per job',9,'Standby generator. Includes gas/propane hookup.'),
('electrical','Electrical','Smoke / CO detector install',50,100,175,'per unit',10,'Hardwired. Battery units cheaper.'),
('electrical','Electrical','Circuit breaker replacement',100,200,350,'per job',11,'Single breaker. AFCI/GFCI breakers cost more.'),
('electrical','Electrical','Dedicated circuit — new',200,350,500,'per circuit',12,'For appliances, shop equipment, etc.'),
('electrical','Electrical','Outdoor / landscape lighting',200,500,1200,'per job',13,'Depends on number of fixtures and wiring distance.'),
('electrical','Electrical','Emergency service — electrical',150,275,400,'per visit',14,'After-hours surcharge plus repair.'),
('electrical','Electrical','Hourly rate — electrician',75,105,140,'per hour',15,'Journeyman rate. Master electrician higher.')
ON CONFLICT DO NOTHING;

-- EXCAVATION (12 services) — Confidence: MEDIUM-HIGH
-- Sources: HomeAdvisor, Fixr, contractor forums, ME/NH permits
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('excavation','Excavation','Site prep — machine work',125,175,250,'per hour',1,'Mini-excavator or backhoe. Operator included.'),
('excavation','Excavation','Septic system installation',8000,15000,25000,'per job',2,'Complete system. Soil test and design separate.'),
('excavation','Excavation','Foundation excavation',2000,5000,10000,'per job',3,'Depends on size, depth, and soil conditions.'),
('excavation','Excavation','Driveway grading',400,800,1500,'per job',4,'Existing gravel driveway. Reshape and grade.'),
('excavation','Excavation','Driveway installation — gravel',1500,3500,7000,'per job',5,'New gravel drive. Base, compaction, surface.'),
('excavation','Excavation','Utility trench',15,25,40,'per linear ft',6,'Water, sewer, electric. Depth affects price.'),
('excavation','Excavation','Land clearing',1500,3500,8000,'per acre',7,'Trees, brush, stumps. Access matters.'),
('excavation','Excavation','Stump removal',100,250,500,'per stump',8,'Size and root system determine price.'),
('excavation','Excavation','French drain / drainage install',1000,3000,6000,'per job',9,'Depends on length and depth.'),
('excavation','Excavation','Retaining wall',20,40,70,'per sq ft',10,'Block, stone, or timber. Height matters.'),
('excavation','Excavation','Fill / gravel delivery',250,450,700,'per load',11,'10-15 yard load. Distance affects price.'),
('excavation','Excavation','Culvert installation',500,1500,3000,'per job',12,'Driveway culvert. Size and material vary.')
ON CONFLICT DO NOTHING;

-- DRAIN CLEANING (10 services) — Confidence: HIGH
-- Sources: HomeAdvisor, Angi, Roto-Rooter comparisons
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('drain','Drain Cleaning','Drain cleaning — single fixture',100,175,275,'per job',1,'Sink, tub, or shower. Snake or auger.'),
('drain','Drain Cleaning','Drain cleaning — main sewer line',150,300,500,'per job',2,'Larger equipment needed. Access point matters.'),
('drain','Drain Cleaning','Camera inspection',150,275,400,'per job',3,'Sewer camera to find breaks, roots, or blockages.'),
('drain','Drain Cleaning','Hydro jetting',300,500,800,'per job',4,'High-pressure water. Best for heavy buildup.'),
('drain','Drain Cleaning','Root removal — mechanical',200,350,600,'per job',5,'Cutting roots inside pipes. May need camera first.'),
('drain','Drain Cleaning','Sewer line repair — spot',500,1500,3500,'per job',6,'Dig and replace a section. Trenchless costs more.'),
('drain','Drain Cleaning','Sewer line replacement — full',3000,7000,15000,'per job',7,'Full line from house to street/tank.'),
('drain','Drain Cleaning','Floor drain cleaning',100,175,250,'per job',8,'Basement floor drains. Common in older homes.'),
('drain','Drain Cleaning','Grease trap service',150,275,400,'per job',9,'Commercial kitchens. Pump and clean.'),
('drain','Drain Cleaning','Emergency after-hours — drain',175,300,450,'per visit',10,'Weekend/night surcharge plus service.')
ON CONFLICT DO NOTHING;

-- GENERAL CONTRACTING (12 services) — Confidence: MEDIUM
-- Sources: HomeAdvisor, Fixr, RSMeans, contractor forums
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('general','General Contracting','Hourly labor rate',55,85,125,'per hour',1,'General carpenter/handyman. Licensed GC higher.'),
('general','General Contracting','Deck building',25,40,65,'per sq ft',2,'Pressure-treated. Composite adds 30-50%.'),
('general','General Contracting','Bathroom remodel — basic',5000,10000,18000,'per job',3,'Fixtures, tile, vanity. Gut reno at high end.'),
('general','General Contracting','Kitchen remodel — basic',8000,20000,40000,'per job',4,'Cabinets, counters, flooring. Custom at high end.'),
('general','General Contracting','Roofing — asphalt shingles',300,450,650,'per square',5,'Per 100 sq ft. Includes tear-off and disposal.'),
('general','General Contracting','Siding replacement — vinyl',5,8,12,'per sq ft',6,'Includes removal of old siding.'),
('general','General Contracting','Window replacement',300,550,900,'per window',7,'Double-hung, vinyl. Custom sizes cost more.'),
('general','General Contracting','Insulation — blown-in',1.00,1.75,3.00,'per sq ft',8,'Attic blown-in cellulose or fiberglass.'),
('general','General Contracting','Drywall — hang and finish',2.50,4.00,6.00,'per sq ft',9,'Includes tape, mud, and prime.'),
('general','General Contracting','Interior painting',200,400,600,'per room',10,'Average 12x12 room. Includes prep and 2 coats.'),
('general','General Contracting','Exterior painting — house',3000,5500,10000,'per job',11,'Depends on size, stories, and prep needed.'),
('general','General Contracting','Flooring installation',4,7,12,'per sq ft',12,'LVP, hardwood, tile. Material cost separate.')
ON CONFLICT DO NOTHING;

-- LANDSCAPING (15 services) — Confidence: HIGH
-- Sources: HomeAdvisor, Angi, Thumbtack, lawn care forums
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('landscaping','Landscaping','Lawn mowing — 1/4 acre',35,50,75,'per visit',1,'Weekly service. Price drops for regular contracts.'),
('landscaping','Landscaping','Lawn mowing — 1/2 acre',50,75,110,'per visit',2,'Includes trimming and blowing.'),
('landscaping','Landscaping','Lawn mowing — 1 acre',75,125,175,'per visit',3,'Large properties. Ride-on mower.'),
('landscaping','Landscaping','Spring cleanup',200,350,600,'per job',4,'Leaf removal, bed cleanup, first mow.'),
('landscaping','Landscaping','Fall cleanup',200,400,700,'per job',5,'Leaf removal, bed prep, final mow.'),
('landscaping','Landscaping','Mulch installation',50,75,100,'per cubic yard',6,'Includes material and spreading.'),
('landscaping','Landscaping','Hedge / shrub trimming',50,100,200,'per job',7,'Depends on number and size of shrubs.'),
('landscaping','Landscaping','Tree trimming',200,500,1200,'per tree',8,'Size and access determine price. Certified arborist more.'),
('landscaping','Landscaping','Tree removal',300,800,2500,'per tree',9,'Depends on size, location, and stump grinding.'),
('landscaping','Landscaping','Stump grinding',100,200,400,'per stump',10,'Size of stump determines price.'),
('landscaping','Landscaping','Lawn aeration',75,150,250,'per job',11,'Core aeration. Price by lawn size.'),
('landscaping','Landscaping','Garden bed installation',500,1200,3000,'per job',12,'Includes soil prep, plants, mulch.'),
('landscaping','Landscaping','Patio / walkway — pavers',12,18,30,'per sq ft',13,'Includes base prep, sand, and pavers.'),
('landscaping','Landscaping','Irrigation system install',2000,4000,7000,'per job',14,'Depends on zone count and lawn size.'),
('landscaping','Landscaping','Snow plowing — residential',35,60,100,'per visit',15,'Per push. Seasonal contracts average $400-800.')
ON CONFLICT DO NOTHING;

-- PROPERTY MAINTENANCE (12 services) — Confidence: MEDIUM-HIGH
-- Sources: HomeAdvisor, property management forums, Thumbtack
INSERT INTO pricing_benchmarks (vertical_key, vertical_label, service_name, price_low, price_typical, price_high, price_unit, sort_order, notes) VALUES
('property_mgmt','Property Maintenance','General handyman',55,80,120,'per hour',1,'Minor repairs, adjustments, odd jobs.'),
('property_mgmt','Property Maintenance','Gutter cleaning',100,200,350,'per job',2,'Single-story home. Multi-story adds 30-50%.'),
('property_mgmt','Property Maintenance','Pressure washing — house',200,400,700,'per job',3,'Vinyl siding, single-story. 2-story more.'),
('property_mgmt','Property Maintenance','Pressure washing — deck/patio',100,200,350,'per job',4,'Prep for staining or sealing.'),
('property_mgmt','Property Maintenance','Pressure washing — driveway',100,200,350,'per job',5,'Concrete or paver driveway.'),
('property_mgmt','Property Maintenance','Window cleaning — exterior',150,300,500,'per job',6,'Depends on window count and access.'),
('property_mgmt','Property Maintenance','Tenant turnover — cleaning',200,400,700,'per unit',7,'Deep clean between tenants. Size matters.'),
('property_mgmt','Property Maintenance','Deck staining / sealing',2,4,6,'per sq ft',8,'Includes prep, stain, and 2 coats.'),
('property_mgmt','Property Maintenance','Snow removal — per visit',40,75,125,'per visit',9,'Walkways, stairs, small parking areas.'),
('property_mgmt','Property Maintenance','Ice dam removal',200,500,1000,'per job',10,'Roof steaming. Dangerous work = premium.'),
('property_mgmt','Property Maintenance','Seasonal HVAC filter swap',25,50,75,'per visit',11,'Quick swap during seasonal checkup.'),
('property_mgmt','Property Maintenance','Property inspection',75,150,250,'per visit',12,'Walk-through and report. Rental properties.')
ON CONFLICT DO NOTHING;
