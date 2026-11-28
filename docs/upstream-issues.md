# Draft upstream issue (not posted)

## Flagsmith/flagsmith: segment API returns 500 when a PERCENTAGE_SPLIT condition has no `property`

Found while building Liftover against self-hosted 2.295.2.

Creating a segment whose rule has `{"operator": "PERCENTAGE_SPLIT", "value": "10"}`:

| `property` | result |
|---|---|
| `""` | 400 `This field may not be blank.` |
| omitted | **500 Server Error** |
| `"$.identity.key"` | 201 |

```
POST /api/v1/projects/<id>/segments/
{"name":"probe","project":<id>,"rules":[{"type":"ALL","conditions":[],"rules":[
  {"type":"ALL","conditions":[{"operator":"PERCENTAGE_SPLIT","value":"10"}],"rules":[]}]}]}
```

Expected: a 400 naming `property` (as the blank case does), or a default of `$.identity.key` since a percentage split has no other meaningful property.
Related, on the CLI side: Flagsmith/flagsmith-cli#122 works around this by sending a null property.
