# Phase 9 — Provider-Agnostic Model Layer

**Status:** implemented. 84 tests. Provider construction verified for Gemini
and Groq; **no live API call was made** — see "What is unverified".

## Why this is better than picking one vendor

Nothing that makes this project interesting is vendor-specific. The verification
gate, customer-scoped SQL, `InjectedToolArg`, the approval `interrupt()`, the
loop-back topology — all of that is LangGraph and `langchain-core`. Only five
function bodies in `llm.py` ever talk to a model.

So the model is a swappable part, and saying so out loud is more honest than
implying one vendor is load-bearing. It also removes the cost barrier: anyone
can clone this and run it against a free tier.

Verified that the security contract is untouched by the swap:

```
                       model-fillable args
check_order_status     ['order_id']
list_my_orders         []
process_refund         ['order_id', 'reason']
```

`customer_id` and `approved` are absent under Gemini exactly as under Claude,
because `InjectedToolArg` is a `langchain-core` feature, not a vendor one.

## Configuration

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `gemini` \| `groq` \| `anthropic` \| `openai` \| `none`. Optional. |
| `LLM_MODEL` | Override the model name for whichever provider is chosen. |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini |
| `GROQ_API_KEY` | Groq |
| `ANTHROPIC_API_KEY` | Claude |
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | Anything OpenAI-compatible |

**Set a key and it just works.** With `LLM_PROVIDER` unset, the first provider
in `PROVIDER_ORDER` that has a key wins — Gemini first, because its free tier
is the least friction for someone cloning the repo. Set nothing and the whole
system runs deterministically, as it has since Phase 2.

`LLM_PROVIDER=none` forces the deterministic path **even when a key is
present**. That exists so the no-model behaviour can be demonstrated on a
machine that has keys exported — which is most machines, and previously meant
unsetting environment variables to show that half of the story.

The `openai` provider is a deliberate catch-all: OpenRouter, Cerebras,
Together, a local Ollama or vLLM all speak the OpenAI wire format. Point
`OPENAI_BASE_URL` at them rather than adding a provider entry each time.

## Configured is not the same as usable

A key set without its SDK installed is a **misconfiguration**, and the first
version of this phase handled it badly: the banner printed `LIVE MODE` while
the system silently ran deterministically. That is the worst of both — it sends
you hunting for a bug in the wrong place.

Two distinct predicates now:

- `TEST_MODE` — no provider was **configured**. Resolved once at import.
- `llm.live()` — a model is **usable right now**.

They differ exactly in the misconfigured case, and the banner and `/api/health`
both use `live()`. A missing SDK now prints, once, to stderr:

```
[llm] gemini key found but langchain-google-genai is not installed.
      Run: pip install langchain-google-genai  (falling back to keyword mode)
Running in TEST MODE - gemini:gemini-3.5-flash UNAVAILABLE - running deterministically
```

It degrades rather than crashing, because a config mistake should never be
worse than having no key at all.

## Model IDs go stale

`gemini-3.5-flash` and `llama-3.1-8b-instant` are the defaults because those
are the IDs the installed SDKs themselves reference — not because they were
looked up. **Model names change and get retired.** If a call fails with
"model not found", set `LLM_MODEL` rather than editing `llm.py`:

```powershell
$env:LLM_MODEL = "whatever-is-current"
```

## What is unverified

Honest boundary. What was tested: provider resolution, the auto-detect order,
forcing off, unknown-provider errors, model-name overrides, the
missing-SDK degradation, and that both `ChatGoogleGenerativeAI` and `ChatGroq`
construct and accept `bind_tools`.

What was **not** tested: a single real API call, to any provider. No key was
available. So whether Gemini returns usable routing labels, resolves "where's
my camera?" correctly, or produces sensible risk briefs is unknown — and
free-tier models are weaker than frontier ones, so the routing prompt may well
need tightening. `normalize_route` already absorbs formatting drift, and every
function falls back on garbage, so the failure mode is degradation rather than
breakage. But it needs a human with a key to judge quality.

Free-tier **rate limits** are also unverified; they change often. The
supervisor runs once per hop, so a two-specialist turn is three routing calls
plus whatever the specialists make. On a tight free tier that adds up, and a
429 currently degrades to keyword mode silently — visible only as a
suspiciously deterministic-looking answer. Surfacing rate-limit fallbacks in
the UI would be a reasonable next step.
