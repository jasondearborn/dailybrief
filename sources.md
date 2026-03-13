# News Aggregator Sources

## Semiconductors / Networking / AI
| Source | Format | Signal Type |
|--------|--------|-------------|
| SemiAnalysis (Dylan Patel) | Substack | Chip industry deep dives, supply chain alpha — established early-signal credibility |
| Fabricated Knowledge | Substack | Semiconductor supply chain — established early-signal credibility |
| The Chip Letter | Substack | Accessible chip industry news |
| The Next Platform | Web/RSS | HPC, AI infrastructure, datacenter — semiconductors/AI infra focus |
| Human Infrastructure | Beehiiv/RSS | Data center physical infrastructure — power, cooling, land, construction. AI infrastructure scale signal. |
| Import AI (Jack Clark) | Substack | Weekly AI research digest |
| The Batch (Andrew Ng) | Newsletter | Applied AI |
| Latent Space | Substack | AI engineering |
| The Register | Web/RSS | Networking, enterprise tech |
| Ars Technica | Web/RSS | Tech, chips, AI |
| IEEE Spectrum | Web/RSS | Engineering-grade technical coverage |
| Stacey on IoT (Stacey Higginbotham) | Web/RSS | Networking, IoT, infrastructure — high editorial credibility |
| Packet Pushers Heavy Networking | Podcast/RSS | Technical networking deep dives (sponsored episodes flagged vendor) |
| Packet Pushers Network Break | Podcast/RSS | Weekly networking news roundup (sponsored episodes flagged vendor) |
| Light Reading | Web/RSS | Telecom and optical networking industry news |
| Wired | Web/RSS | Broad tech trends |
| MIT Tech Review | Web/RSS | Research to market pipeline |
| Hankyung (Korea Economic Daily) | Web/RSS | Regional primary source — Samsung, SK Hynix, DRAM/HBM early signal |

## Research / Pre-Publication
| Source | Format | Signal Type |
|--------|--------|-------------|
| arxiv.org (cs.AI, cs.NI) | RSS | Raw research signal, pre-publication — flag as unreviewed |

## Vendor Feeds (treat as primary source, not neutral)
| Source | Format | Notes |
|--------|--------|-------|
| Nvidia Blog | RSS | GPU/AI infrastructure |
| TSMC Newsroom | RSS | Fab capacity, process node updates |
| Juniper Networks Blog | RSS | Networking |
| HPE/Juniper Mist Blog | RSS | Networking, Wi-Fi, cloud-managed infrastructure |
| Cisco Newsroom | RSS | Networking |
| Arista Networks Blog | RSS | Networking, cloud/hyperscaler infrastructure |

## China / Export Control Watch
| Source | Format | Signal Type |
|--------|--------|-------------|
| South China Morning Post (SCMP) | Web/RSS | SMIC, Huawei, China tech policy — state-adjacent, read critically |
| Yicai Global | Web/RSS | Chinese financial and industry news, English edition |

## Finance / Retirement
| Source | Format | Signal Type |
|--------|--------|-------------|
| Epsilon Theory (Ben Hunt) | Substack | Narrative manipulation analysis, high trust |
| The Diff (Byrne Hobart) | Substack | Independent, second-order macro/tech thinking |
| Calculated Risk (Bill McBride) | Blog/RSS | Macro, housing, economic data — strong forecasting track record |
| Net Interest (Marc Rubinstein) | Substack/RSS | Financial sector deep dives |
| Verdad Research | Web | Quantitative, free research, no agenda |
| Damodaran Online | Blog | Valuation fundamentals, no financial incentive |
| Acquired Podcast | Podcast/RSS | Deep company analysis |
| SEC EDGAR | Direct feed | Primary source, no media layer |
| SEC Form 4 (insider transactions) | Direct feed | Underused alpha signal |
| Federal Reserve | RSS | Primary source for macro/rate signals |
| Financial Times | Web/RSS | International macro context |
| Edward Jones Weekly Market Update | Web/RSS | Senior economist wrap (equities, bonds, macro, Fed posture). Site-wide RSS at /rss.xml. **Institutional bias — cross-check against Calculated Risk.** |
| Charles Schwab Market Update | Web (no RSS) | Schwab strategist commentary. No public RSS; WAF blocks scraping. Manual review only. **Institutional bias.** |
| Morgan Stanley Insights | Web (no RSS) | Named strategists (Mike Wilson, Andrew Sheets, others); includes podcast transcripts. No RSS feed. Manual review only. **Institutional bias.** |

## Local Safety (Trend-Oriented)
| Source | Format | Signal Type |
|--------|--------|-------------|
| CalFire | RSS | Fire risk, active incidents, seasonal outlooks |
| KQED News | RSS | Bay Area investigative, civil unrest, policy |
| Mission Local | RSS | SF ground-level civil unrest, immigration enforcement |
| Oaklandside | RSS | East Bay community safety trends |
| Berkeleyside | RSS | Berkeley/East Bay community, local policy, civil unrest |
| ACLU of Northern CA | RSS | Civil liberties, ICE/immigration enforcement patterns |
| ICE ERO Press Releases | RSS | Federal enforcement activity direct feed |
| DHS Newsroom | RSS | Federal policy with local impact |
| BayCity News | RSS | Bay Area courts, crime, government |
| CAL FIRE Incident Feed | RSS | Active fire perimeters |
| PurpleAir API | API | Air quality — fire season essential |

## Culture / Zeitgeist
| Source | Format | Signal Type |
|--------|--------|-------------|
| Garbage Day | Substack | Internet culture, early trend signal |
| Platformer (Casey Newton) | Substack | Tech industry culture, policy, Big Tech behavior |
| The Information | Web | Insider tech industry dynamics |
| Bloomberg Businessweek | RSS | Business culture, macro consumer trends |
| Puck News | Web | Power dynamics in tech, media, politics |
| The Rundown AI | Newsletter | Mainstream AI tool/use case awareness — customer conversation fuel |

## Reddit (Zeitgeist Signal Only — not factual sources)
| Source | Format | Signal Type |
|--------|--------|-------------|
| r/SecurityAnalysis | Reddit RSS | Higher quality investment discussion |
| r/MachineLearning | Reddit RSS | Research community signal |
| r/networking | Reddit RSS | Practitioner zeitgeist |

## Triangulation Notes
- Flag any story covered by only 1-2 sources as low confidence, UNLESS source has established early-signal track record
- Track per-source hit rate on early stories that later get broad confirmation — weight accordingly
- SemiAnalysis (Dylan Patel) and Fabricated Knowledge have demonstrated early-signal credibility on chips — treat single-source stories from them as medium confidence, not low
- Epsilon Theory and Verdad are useful cross-checks against mainstream financial narratives
- Vendor feeds are always primary sources with an agenda — never cite alone
- arxiv signals are pre-peer-review — flag accordingly
- Reddit is zeitgeist signal only, not factual source
- SCMP and Yicai are China-side sources — state-adjacent, read critically, useful for export control and supply chain signals not covered by Western media
- Distinguish between: (1) single source early, (2) single source only — log outcomes over time to build source reputation scores
- Stories where high-credibility sources diverge are higher priority than consensus stories — divergence is signal
- For geopolitically sensitive stories (Middle East, US federal policy, China/Taiwan), require 3+ independent sources before elevating confidence — state influence on media is highest in these areas

## Sources Excluded (and why)
- CNBC, Bloomberg TV, Yahoo Finance — narrative-driven, manipulation risk
- Zero Hedge — sensationalist, unreliable
- Mainstream social media trending — noise floor too high
- Jerusalem Post, i24 News, Arutz Sheva — Israeli state-adjacent media
- RT, Tass, Sputnik News, Pravda — Russian state media
- Breitbart, Daily Wire, Washington Examiner — MAGA/Trump-aligned, narrative over fact
- New York Post — Murdoch-owned, editorial agenda
- Newsmax, OAN — propaganda risk
- Any source with documented AIPAC funding ties
- Business Insider — low editorial standards, clickbait finance content

