# TEMPORARY — remember to turn this off

**`DEEPSEEK_CACHE=1` in `.env` is a cost stopgap, not a permanent setting.**

It is on because a repeat of the same question was being paid for twice. With it
on, an identical request is answered from `data/tmp/llm_cache/` instead of the
API. Re-analysing a song changes the request, so real new questions still go to
the model — nothing stale can be served.

## The reminder

**Turn it off when:** you want to re-read the model's output with fresh eyes
(a cached answer is the same answer you already have, so repeated "Name sections"
will keep returning the first wording), or when the bill stops mattering.

```
# in .env
DEEPSEEK_CACHE=0
```

Then restart the server. Confirm it took:

```
curl -s http://127.0.0.1:8848/api/health | python3 -c 'import sys,json;print(json.load(sys.stdin)["llm_cache"])'
# {"enabled": false, ...}
```

## What it does *not* do

It does not stop the model being *called*. The eight interpretation endpoints
(`/insight`, `/insight/window`, `/insight/event`, `/insight/energy`,
`/insight/compare`, `/insight/question`, `/insight/lessons`,
`/sections/name`) each send a request the first time they are asked about
something new. All of them are behind buttons, so nothing calls them on a timer.

To stop the model being called at all, blank the key:

```
DEEPSEEK_API_KEY=
```

The app is fully usable with it blank — every measurement, section and timeline
is computed locally, and the reasoning panel says the key is not set rather than
pretending.
