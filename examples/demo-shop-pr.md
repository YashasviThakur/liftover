## Move feature flags to Flagsmith (from env, unleash)

- 10 flags now read from Flagsmith, 4 targeting segments, environments: development, production. Everything created is tagged `migrated`.
- Unknown flags still answer off (default_flag_handler), as before.
- Percentage rollouts and variants keep their shares, but Flagsmith hashes users differently, so some users switch bucket once (counted below).

### Parity check: 4000 sampled users per environment, both sides evaluated by their own SDK

| flag | environment | check | result |
|---|---|---|---|
| age_gate | development | exact | same for every user |
| beta_dashboard | development | exact | same for every user |
| checkout_button | development | statistical | within 4 pts (gap 2.8); Unleash add_to_bag 19.3%, buy_now 49.6%, get_it_today 31.1% / Flagsmith add_to_bag 21.4%, buy_now 50.3%, get_it_today 28.3%; 2475 users switch |
| dark_mode | development | exact | same for every user |
| holiday_banner | development | exact | same for every user |
| maintenance_mode | development | exact | same for every user |
| new_checkout | development | exact | same for every user |
| new_onboarding | development | exact | same for every user |
| pro_reports | development | exact | same for every user |
| upload_limit_mb | development | exact | same for every user |
| age_gate | production | exact | same for every user |
| beta_dashboard | production | exact | same for every user |
| checkout_button | production | statistical | within 4 pts (gap 2.2); Unleash add_to_bag 19.3%, buy_now 49.6%, get_it_today 31.1% / Flagsmith add_to_bag 20.8%, buy_now 50.3%, get_it_today 28.9%; 2391 users switch |
| dark_mode | production | exact | same for every user |
| holiday_banner | production | exact | same for every user |
| maintenance_mode | production | exact | same for every user |
| new_checkout | production | statistical | within 4 pts (gap 0.6); Unleash off 75.4%, on 24.6% / Flagsmith off 74.8%, on 25.2%; 1454 users switch |
| new_onboarding | production | exact | same for every user |
| pro_reports | production | exact | same for every user |
| upload_limit_mb | production | exact | same for every user |

Not migrated: legacy_search (retire), recommendations_v2 (skip)

### Rollback
Revert this PR. To also remove what was created in Flagsmith: `python apply.py --rollback --execute`.
Keep the old system running until this has been live for a while; retire it in a later change.
