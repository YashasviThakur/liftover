### Parity check: 4000 sampled users per environment, both sides evaluated by their own SDK

| flag | environment | check | result |
|---|---|---|---|
| age_gate | development | exact | same for every user |
| beta_dashboard | development | exact | same for every user |
| checkout_button | development | statistical | within 4 pts (gap 1.1); LaunchDarkly add_to_bag 19.5%, buy_now 51.2%, get_it_today 29.3% / Flagsmith add_to_bag 19.6%, buy_now 50.1%, get_it_today 30.3%; 2447 users switch |
| maintenance_mode | development | exact | same for every user |
| new_checkout | development | exact | same for every user |
| pricing_tier | development | exact | same for every user |
| pro_reports | development | exact | same for every user |
| age_gate | production | exact | same for every user |
| beta_dashboard | production | exact | same for every user |
| checkout_button | production | statistical | within 4 pts (gap 2); LaunchDarkly add_to_bag 19.5%, buy_now 51.2%, get_it_today 29.3% / Flagsmith add_to_bag 20.5%, buy_now 49.2%, get_it_today 30.4%; 2419 users switch |
| maintenance_mode | production | exact | same for every user |
| new_checkout | production | statistical | within 4 pts (gap 0.8); LaunchDarkly off 75.2%, on 24.8% / Flagsmith off 74.4%, on 25.6%; 1486 users switch |
| pricing_tier | production | exact | same for every user |
| pro_reports | production | exact | same for every user |

Not migrated: legacy_search (retire), upsell_banner (skip)
