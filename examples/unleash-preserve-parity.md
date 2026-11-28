### Parity check: 4000 sampled users per environment, both sides evaluated by their own SDK

| flag | environment | check | result |
|---|---|---|---|
| age_gate | development | exact | same for every user |
| beta_dashboard | development | exact | same for every user |
| checkout_button | development | statistical | within 4 pts (gap 1.1); Unleash add_to_bag 19.3%, buy_now 49.6%, get_it_today 31.1% / Flagsmith add_to_bag 20.4%, buy_now 49.1%, get_it_today 30.5%; 2437 users switch |
| dark_mode | development | exact | same for every user |
| holiday_banner | development | exact | same for every user |
| maintenance_mode | development | exact | same for every user |
| new_checkout | development | exact | same for every user |
| new_onboarding | development | exact | same for every user |
| pro_reports | development | exact | same for every user |
| upload_limit_mb | development | exact | same for every user |
| age_gate | production | exact | same for every user |
| beta_dashboard | production | exact | same for every user |
| checkout_button | production | statistical | within 4 pts (gap 1.1); Unleash add_to_bag 19.3%, buy_now 49.6%, get_it_today 31.1% / Flagsmith add_to_bag 20.2%, buy_now 49.8%, get_it_today 30%; 2358 users switch |
| dark_mode | production | exact | same for every user |
| holiday_banner | production | exact | same for every user |
| maintenance_mode | production | exact | same for every user |
| new_checkout | production | exact | same for every user |
| new_onboarding | production | exact | same for every user |
| pro_reports | production | exact | same for every user |
| upload_limit_mb | production | exact | same for every user |

Not migrated: legacy_search (retire), recommendations_v2 (skip)
