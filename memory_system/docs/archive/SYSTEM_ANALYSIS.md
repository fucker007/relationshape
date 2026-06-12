# Person Graph Memory System — Comprehensive Analysis

## Executive Summary

This document analyzes the Person Graph memory system from four critical perspectives:
1. **Human Memory Habits** — How the system aligns with natural cognitive patterns
2. **Philosophical Memory Patterns** — Abstract design principles and their implications
3. **System Stability & Robustness** — Failure modes and edge cases
4. **User Satisfaction Issues** — Pain points and unmet expectations

---

## Part 1: Human Memory Habits Alignment

### 1.1 Critical Misalignment: Semantic Decay vs. Emotional Persistence

**Problem**: The system applies uniform recency decay (`score *= 1/(1+days*0.01)`) to all memory types.

**Human Reality**: Emotional memories don't decay like factual memories. A traumatic event from 5 years ago remains vivid; a mundane daily activity from yesterday fades quickly.

**Impact**:
- Negative emotional events (conflicts, failures) should have **higher persistence** than daily routines
- Current system treats "打篮球" (played basketball) same as "被欺负" (bullied) — both decay equally
- User expectation: "Remember when I was bullied?" should surface even after months; "What did I eat yesterday?" should fade

**Fix Required**:
```python
# Current (wrong)
recency_factor = 1.0 / (1.0 + days * 0.01)

# Should be (emotion-aware)
if event_type == "conflict" or emotion in ["生气", "难过", "害怕"]:
    recency_factor = 1.0 / (1.0 + days * 0.001)  # 10x slower decay
elif event_type == "daily":
    recency_factor = 1.0 / (1.0 + days * 0.05)   # 5x faster decay
else:
    recency_factor = 1.0 / (1.0 + days * 0.01)   # default
```

---

### 1.2 Missing: Episodic vs. Semantic Memory Distinction

**Problem**: System conflates two memory types humans naturally separate:
- **Episodic**: "What happened?" (specific events with time/place/people)
- **Semantic**: "What is true?" (facts, preferences, identity)

**Current System**:
- Events table: episodic ✓
- PersonNode attributes: semantic ✓
- **But**: No distinction in recall weighting

**Human Reality**:
- When asked "你喜欢什么?" (What do you like?), humans recall **semantic** preference, not episodic "I ate pizza yesterday"
- When asked "最近发生了什么?" (What happened recently?), humans recall **episodic** events, not "I like pizza"

**Impact**:
- Query "我喜欢什么运动" (What sports do I like?) should prioritize PersonNode.preferences, not events
- Current system treats both equally in vector search
- User frustration: "Why is it telling me about the time I played basketball instead of just saying I like basketball?"

**Fix Required**:
```python
# In retrieval/graph_recall.py
if intent == "preference":
    # Direct lookup, no vector search
    prefs = await gs.get_person_node(person_id)
    return prefs["preferences"]  # Skip events entirely
elif intent == "event":
    # Vector search events only
    return await vector_search_events(...)
```

---

### 1.3 Missing: Contextual Forgetting (Interference)

**Problem**: System has no mechanism for **interference** — when new information contradicts old information.

**Human Reality**: If I say "I hate basketball" after months of "I love basketball," the new statement should **suppress** the old one, not coexist.

**Current System**:
- PersonNode.preferences is a list: `[{item: "basketball", strength: 0.7}, ...]`
- When user says "我讨厌篮球" (I hate basketball), system adds to aversions
- **Result**: Both "love basketball" and "hate basketball" exist simultaneously
- Recall returns both, confusing the LLM

**Impact**:
- User says: "我以前喜欢篮球，但现在讨厌了"
- System stores: preferences=[basketball], aversions=[basketball]
- LLM sees both and doesn't know which is current
- User frustration: "Why doesn't it remember I changed my mind?"

**Fix Required**:
```python
# In pipeline/profile_updater.py
async def update_preference(person_id, item, is_preference=True):
    node = await gs.get_person_node(person_id)

    if is_preference:
        # Remove from aversions if exists
        node["aversions"] = [a for a in node["aversions"] if a["item"] != item]
        # Add/update in preferences
        node["preferences"] = add_or_update(node["preferences"], item)
    else:
        # Remove from preferences if exists
        node["preferences"] = [p for p in node["preferences"] if p["item"] != item]
        # Add/update in aversions
        node["aversions"] = add_or_update(node["aversions"], item)

    await gs.update_person_node(person_id, node)
```

---

### 1.4 Missing: Temporal Context Collapse

**Problem**: System stores absolute timestamps but loses **temporal context** (morning vs. afternoon, weekday vs. weekend, season).

**Human Reality**: "I'm tired" means different things:
- 9 PM on Friday = normal
- 9 AM on Monday = concerning
- During exam season = expected
- During summer = unusual

**Current System**:
- Event.event_time: `2026-03-27T14:30:00Z`
- No metadata about day-of-week, time-of-day, season, or context

**Impact**:
- Query "我最近怎么样?" (How am I lately?) returns events without temporal clustering
- User sees: "tired (3/20), tired (3/21), tired (3/22)" — looks like depression
- Actually: all 9 PM, all normal
- System can't distinguish "tired every evening" (normal) from "tired all day" (concerning)

**Fix Required**:
```python
# Add to Event model
class Event(BaseModel):
    event_time: datetime
    time_of_day: Literal["morning", "afternoon", "evening", "night"]
    day_of_week: Literal["Monday", ..., "Sunday"]
    season: Literal["spring", "summer", "fall", "winter"]
    is_school_day: bool  # vs. weekend/holiday
    is_exam_period: bool
```

---

### 1.5 Missing: Emotional Trajectory Tracking

**Problem**: System records individual emotions but not **emotional arcs** (getting better/worse over time).

**Human Reality**: "I was sad for 3 days but got better" is different from "I'm sad and it's getting worse."

**Current System**:
- DailyEmotion table: one row per day
- No tracking of trend (improving/declining/stable)

**Impact**:
- User says: "我最近一直很难过" (I've been sad lately)
- System returns: 5 sad events from past week
- **Missing**: Is it getting better or worse? Is this normal for this time of year?
- User frustration: "It's not tracking my emotional state, just listing sad events"

**Fix Required**:
```python
# Add to DailyEmotion
class DailyEmotion(BaseModel):
    emotion_distribution: dict[str, float]
    trend: Literal["improving", "stable", "declining"]  # vs. 7-day baseline
    baseline_emotion: str  # typical emotion for this person
    is_anomalous: bool  # significantly different from baseline
```

---

## Part 2: Philosophical Memory System Patterns

### 2.1 The Identity Paradox: Fixed vs. Fluid Self

**Problem**: PersonNode treats identity as **fixed** (name, age, school), but humans experience identity as **fluid** (beliefs, values, self-perception).

**Current System**:
- PersonNode.identity: `{name: "小明", age: 10, school: "阳光小学"}`
- These are treated as immutable facts
- **But**: A 10-year-old's self-perception changes weekly

**Philosophical Issue**:
- System assumes: "I am 10 years old" = permanent fact
- Reality: "I am 10 years old" = temporary state; "I am brave" = evolving self-concept
- System conflates **demographic facts** with **identity beliefs**

**Impact**:
- User says: "我其实很胆小" (I'm actually quite timid)
- System stores: personality=[{trait: "timid"}]
- But also has: events=[conflict, achievement] suggesting confidence
- **Contradiction**: System can't reconcile "I'm timid" with "I won the competition"
- User frustration: "It doesn't understand that I'm timid but still try hard"

**Fix Required**:
```python
# Separate demographic from identity
class PersonNode(BaseModel):
    # Demographic (immutable)
    demographics: {
        name: str,
        age: int,
        birthday: date,
        school: str,
        grade: str,
    }

    # Identity (evolving self-concept)
    self_perception: {
        traits: list[{trait, confidence, evidence_count}],
        values: list[{value, importance}],
        aspirations: list[{goal, priority}],
    }

    # Behavioral (observable patterns)
    behaviors: list[{pattern, frequency, context}]
```

---

### 2.2 The Narrative Problem: Events vs. Stories

**Problem**: System stores **events** (discrete facts) but humans remember **stories** (connected narratives).

**Current System**:
- Events are independent: `[Event1, Event2, Event3]`
- No causal links except `Event.caused_by` (rarely populated)
- No narrative arc

**Philosophical Issue**:
- Human memory: "I was bullied → I became shy → I avoided social events → I felt lonely"
- System memory: `[bullied_event, shy_event, avoided_event, lonely_event]` (disconnected)
- System can't answer: "Why am I shy?" (needs narrative chain)

**Impact**:
- User asks: "为什么我不想和别人玩?" (Why don't I want to play with others?)
- System returns: events about avoiding social situations
- **Missing**: The causal story (bullying → shyness → avoidance)
- User frustration: "It's not explaining why, just listing what happened"

**Fix Required**:
```python
# Add narrative layer
class Narrative(BaseModel):
    narrative_id: UUID
    owner_id: UUID
    title: str  # "Why I'm shy"
    events: list[UUID]  # ordered chain of event_ids
    causal_chain: list[{from_event_id, to_event_id, relation_type}]
    # relation_type: "caused", "triggered", "reinforced", "resolved"
    theme: str  # "social_anxiety", "self_confidence", etc.
    resolution: str | None  # how it was resolved, if at all
```

---

### 2.3 The Authenticity Problem: Stated vs. Revealed Preferences

**Problem**: System treats **stated preferences** (what user says) as equal to **revealed preferences** (what user does).

**Current System**:
- User says: "我喜欢运动" → PersonNode.preferences += "运动"
- User plays basketball 50 times → Events show basketball 50 times
- **But**: System doesn't distinguish between stated and revealed

**Philosophical Issue**:
- Humans often say one thing but do another
- "I like healthy food" (stated) vs. "I eat junk food" (revealed)
- System should track both and flag contradictions

**Impact**:
- User says: "我喜欢读书"
- But events show: 0 reading events in 3 months
- System stores preference but doesn't notice contradiction
- LLM gets confused: "Does this person like reading or not?"
- User frustration: "It's not tracking what I actually do, just what I say"

**Fix Required**:
```python
# Track both stated and revealed
class Preference(BaseModel):
    item: str
    stated: bool  # user explicitly said they like it
    revealed: float  # frequency in events (0.0-1.0)
    confidence: float  # how confident is this preference?
    # confidence = 1.0 if stated==revealed
    # confidence = 0.5 if stated!=revealed (contradiction)
    # confidence = 0.3 if only stated, no events
    # confidence = 0.8 if only revealed, consistent pattern
```

---

### 2.4 The Forgetting Problem: Intentional vs. Unintentional

**Problem**: System applies uniform decay, but humans **intentionally forget** (suppression) vs. **unintentionally forget** (decay).

**Current System**:
- All memories decay equally: `score *= 1/(1+days*0.01)`
- No distinction between "I want to forget this" and "I naturally forgot this"

**Philosophical Issue**:
- Trauma survivors intentionally suppress memories (not decay, but active suppression)
- Embarrassing moments are intentionally forgotten
- System treats these as normal decay

**Impact**:
- User experienced bullying 6 months ago
- System: "This memory is old, decay it"
- User: "I'm trying to forget this, please don't remind me"
- System surfaces it anyway because decay is slow
- User frustration: "Why does it keep bringing up painful memories?"

**Fix Required**:
```python
# Add suppression flag
class Event(BaseModel):
    is_suppressed: bool = False  # user wants to forget
    suppression_reason: str | None  # "trauma", "embarrassment", "irrelevant"

# In recall
if event.is_suppressed:
    # Don't surface unless explicitly asked
    return None
```

---

## Part 3: System Stability & Robustness

### 3.1 Critical: Vector Search Failure Modes

**Problem**: System relies on BGE-M3 embeddings for semantic search, but embeddings can fail silently.

**Failure Modes**:
1. **Vocabulary Mismatch**: Query "我去过哪些地方" (places I've been) has no keywords in event summaries
   - Events: "和小华在公园打篮球" (played basketball in park with Xiahua)
   - Query embedding: [0.1, 0.2, ...] (generic "places" vector)
   - Event embedding: [0.3, 0.4, ...] (specific "basketball" vector)
   - Cosine similarity: 0.45 (below threshold)
   - **Result**: No recall, user sees empty response

2. **Polysemy**: "打" means both "play" and "hit"
   - Event: "和小华打架" (fought with Xiahua)
   - Query: "我喜欢打篮球" (I like playing basketball)
   - Both have "打", embeddings might conflate them
   - **Result**: Irrelevant recall

3. **Negation Blindness**: BGE-M3 doesn't handle negation well
   - Event: "我不喜欢吃辣" (I don't like spicy food)
   - Query: "我喜欢吃什么?" (What do I like to eat?)
   - Embedding treats "喜欢" and "不喜欢" similarly
   - **Result**: False positive recall

**Current Mitigation**: None. System assumes embeddings work.

**Fix Required**:
```python
# Add fallback to keyword search
async def recall_with_fallback(query, person_id, redis, pg):
    # Try vector search first
    vector_results = await vector_search_events(query)

    if not vector_results or confidence == "low":
        # Fallback to keyword search
        keywords = extract_keywords(query)
        keyword_results = await keyword_search_events(person_id, keywords)
        return keyword_results

    return vector_results
```

---

### 3.2 Critical: Event Merging Brittleness

**Problem**: Event merging uses LLM semantic judgment (82% accuracy), but 18% error rate compounds over time.

**Failure Modes**:
1. **False Positives** (merge when shouldn't):
   - Event A: "和小华打篮球" (played basketball with Xiahua)
   - Event B: "和小华打架" (fought with Xiahua)
   - LLM: "Both involve 小华 and 打, probably same event" → MERGE
   - **Result**: Conflated positive and negative events

2. **False Negatives** (don't merge when should):
   - Event A: "我考试得了100分" (I got 100 on exam)
   - Event B: "我很开心" (I'm very happy)
   - LLM: "Different events" → NO MERGE
   - **Result**: Duplicate events, inflated event count

3. **Cascading Errors**:
   - Day 1: Merge A+B (wrong)
   - Day 2: Try to merge merged(A+B)+C
   - Error compounds: 82% × 82% = 67% accuracy after 2 merges

**Current Mitigation**: None. Merged events are permanent.

**Fix Required**:
```python
# Add merge audit trail
class Event(BaseModel):
    merge_history: list[{
        merged_with_event_id: UUID,
        merge_confidence: float,  # 0.82 from LLM
        merged_at: datetime,
        can_unmerge: bool,  # if confidence < 0.7
    }]

# Periodic audit
async def audit_merges():
    for event in events_with_merges:
        if event.merge_confidence < 0.7:
            # Flag for manual review
            await flag_for_review(event)
```

---

### 3.3 Critical: Relationship Sentiment Drift

**Problem**: Relationship.sentiment is updated by events but has no bounds checking or anomaly detection.

**Failure Modes**:
1. **Unbounded Drift**:
   - User has 100 positive events with friend → sentiment = +1.0
   - One conflict event → sentiment -= 0.2 → +0.8
   - But if 1000 positive events, one conflict barely moves sentiment
   - **Result**: Sentiment doesn't reflect current state

2. **Recency Blindness**:
   - User had 50 conflicts with friend 1 year ago → sentiment = -0.5
   - User has 10 positive events with friend recently → sentiment = -0.3
   - **But**: System doesn't know recent events are more important
   - User expectation: "We're friends again, sentiment should be positive"
   - System: "Still negative because of old conflicts"

3. **No Anomaly Detection**:
   - Relationship sentiment suddenly drops from +0.8 to -0.9
   - System: "OK, update it"
   - **Missing**: "This is a 1.7-point drop, something major happened"
   - User frustration: "It's not tracking the crisis in my friendship"

**Current Mitigation**: None.

**Fix Required**:
```python
# Add bounds and anomaly detection
class Relationship(BaseModel):
    sentiment: float  # -1.0 to +1.0
    sentiment_trend: Literal["improving", "stable", "declining"]
    sentiment_volatility: float  # std dev of recent changes
    last_major_event: {event_id, change_magnitude, timestamp}

async def update_sentiment(rel_id, delta):
    rel = await get_relationship(rel_id)
    old_sentiment = rel.sentiment
    new_sentiment = clamp(rel.sentiment + delta, -1.0, 1.0)

    # Anomaly detection
    if abs(new_sentiment - old_sentiment) > 0.5:
        await flag_anomaly(rel_id, f"Sentiment jump: {old_sentiment} → {new_sentiment}")

    rel.sentiment = new_sentiment
    rel.sentiment_trend = compute_trend(rel.sentiment_history)
    await update_relationship(rel)
```

---

### 3.4 Critical: Profile Updater Race Conditions

**Problem**: ProfileUpdater updates PersonNode attributes asynchronously, but no locking mechanism.

**Failure Modes**:
1. **Lost Updates**:
   - User says: "我叫小明" → ProfileUpdater.update_from_attributes()
   - User says: "我叫小刚" → ProfileUpdater.update_from_attributes()
   - Both run concurrently, both read PersonNode.identity
   - Both write back, one overwrites the other
   - **Result**: One name change is lost

2. **Inconsistent State**:
   - ProfileUpdater updates PersonNode.preferences
   - Meanwhile, EventMerger updates PersonNode.total_events
   - Both write to same row, one update is lost
   - **Result**: Inconsistent state

**Current Mitigation**: None. No locking.

**Fix Required**:
```python
# Add optimistic locking
class PersonNode(BaseModel):
    version: int = 0  # increment on each update

async def update_person_node(person_id, updates):
    node = await get_person_node(person_id)
    old_version = node.version

    # Apply updates
    node.update(updates)
    node.version += 1

    # Optimistic lock check
    result = await pg.execute(
        "UPDATE person_nodes SET ... WHERE person_id=$1 AND version=$2",
        person_id, old_version
    )

    if result.rowcount == 0:
        # Version mismatch, retry
        raise ConcurrencyError("Retry update")
```

---

### 3.5 Critical: Redis Cache Invalidation

**Problem**: Redis caches PersonNode profiles and recall results, but invalidation is incomplete.

**Failure Modes**:
1. **Stale Profile Cache**:
   - User says: "我喜欢吃辣" → PersonNode.preferences updated
   - ProfileUpdater calls `redis.invalidate_recall_cache(person_id)`
   - **But**: Profile cache key is different from recall cache key
   - LLM gets stale profile: "喜欢吃辣" is missing
   - User frustration: "I just told it I like spicy food, why doesn't it remember?"

2. **Cascading Invalidation**:
   - Event is merged → PersonNode.total_events changes
   - Need to invalidate: profile cache, recall cache, relationship cache
   - Current code only invalidates one
   - **Result**: Inconsistent caches

**Current Mitigation**: Partial invalidation in api/routers/memories.py line 69.

**Fix Required**:
```python
# Centralized cache invalidation
class CacheInvalidator:
    async def invalidate_person(self, person_id: str):
        """Invalidate all caches related to a person"""
        await redis.delete(f"profile:{person_id}")
        await redis.delete(f"recall_cache:{person_id}:*")
        await redis.delete(f"relationships:{person_id}:*")
        await redis.delete(f"daily_emotions:{person_id}:*")
        await redis.delete(f"focus:{person_id}")
```

---

## Part 4: User Satisfaction Issues

### 4.1 Missing: Explanation Layer

**Problem**: System returns memories but doesn't explain **why** they're relevant.

**Current System**:
- Query: "我最近怎么样?"
- Response: `[Event1, Event2, Event3]`
- **Missing**: Why these events? What's the pattern?

**User Expectation**:
- "You've been stressed about exams (3 events), but also had fun with friends (2 events)"
- Not just: "Here are 5 events"

**Fix Required**:
```python
class RecallResponse(BaseModel):
    memories: list[MemorySearchResult]
    explanation: str  # "You've been stressed about exams..."
    pattern: str  # "stress_with_social_relief"
    confidence: Literal["high", "uncertain", "low", "empty"]
```

---

### 4.2 Missing: Temporal Clustering

**Problem**: System returns events in relevance order, not temporal order.

**Current System**:
- Query: "我最近发生了什么?"
- Response: `[Event_3days_ago, Event_1day_ago, Event_2days_ago]` (by relevance)
- User reads: "Confusing, what's the timeline?"

**User Expectation**:
- Events grouped by day/week, in chronological order
- "This week: [Mon, Tue, Wed events]"

**Fix Required**:
```python
class RecallResponse(BaseModel):
    memories_by_period: {
        "today": [...],
        "this_week": [...],
        "this_month": [...],
    }
```

---

### 4.3 Missing: Contradiction Detection

**Problem**: System doesn't flag contradictions in user's stated preferences.

**Current System**:
- User says: "我喜欢运动"
- User says: "我讨厌运动"
- System stores both
- LLM sees both and doesn't know which is true

**User Expectation**:
- "You said you like sports, but also said you hate sports. Which is it?"

**Fix Required**:
```python
async def detect_contradictions(person_id):
    node = await get_person_node(person_id)
    contradictions = []

    for pref in node.preferences:
        for aver in node.aversions:
            if pref["item"] == aver["item"]:
                contradictions.append({
                    "item": pref["item"],
                    "stated_preference": pref,
                    "stated_aversion": aver,
                })

    return contradictions
```

---

### 4.4 Missing: Emotional Support

**Problem**: System tracks emotions but doesn't provide support or context.

**Current System**:
- User says: "我很难过"
- System stores: Event(emotion="难过")
- **Missing**: Acknowledgment, support, or context

**User Expectation**:
- "I notice you've been sad for 3 days. Is everything OK?"
- Not just: "Stored your sadness"

**Fix Required**:
```python
# Add emotional support layer
async def check_emotional_wellbeing(person_id):
    daily_emotions = await get_daily_emotions(person_id, days=7)

    # Detect concerning patterns
    if all(e.dominant_emotion in ["sad", "angry"] for e in daily_emotions[-3:]):
        return {
            "concern": "sustained_negative_emotion",
            "message": "I've noticed you've been sad for 3 days. Want to talk about it?",
            "suggestion": "Let's focus on positive events or things you enjoy",
        }
```

---

### 4.5 Missing: Privacy & Control

**Problem**: System stores everything, but users may want to delete or hide memories.

**Current System**:
- Event.is_deleted flag exists but is rarely used
- No user-facing delete mechanism
- No privacy controls

**User Expectation**:
- "Delete this embarrassing memory"
- "Hide this from the AI"
- "This is private, don't use it in responses"

**Fix Required**:
```python
class Event(BaseModel):
    is_deleted: bool = False
    is_private: bool = False  # don't use in recall
    is_archived: bool = False  # keep but don't surface
    deletion_reason: str | None  # "embarrassment", "privacy", "irrelevant"

# In recall
if event.is_private or event.is_archived:
    # Don't surface unless explicitly asked
    return None
```

---

## Summary: Critical Issues by Priority

### P0 (System Breaking)
1. **Vector search failure modes** — No fallback when embeddings fail
2. **Event merging brittleness** — 18% error rate compounds
3. **Race conditions in ProfileUpdater** — Lost updates
4. **Cache invalidation incomplete** — Stale data served

### P1 (Major UX Issues)
1. **Semantic vs. episodic memory confusion** — Wrong recall for preference queries
2. **Emotional decay uniform** — Traumatic memories decay too fast
3. **Contradiction detection missing** — Conflicting preferences not flagged
4. **Explanation layer missing** — Users don't understand why memories were recalled

### P2 (Design Flaws)
1. **Identity paradox** — Fixed vs. fluid self not distinguished
2. **Narrative layer missing** — Events not connected in causal chains
3. **Stated vs. revealed preferences** — Not tracked separately
4. **Intentional forgetting** — No suppression mechanism

### P3 (Nice to Have)
1. **Temporal context collapse** — No day-of-week, season metadata
2. **Emotional trajectory** — No trend tracking
3. **Emotional support** — No wellbeing checks
4. **Privacy controls** — No user-facing delete

---

## Recommended Implementation Order

1. **Week 1**: Fix P0 issues (vector search fallback, race conditions, cache invalidation)
2. **Week 2**: Fix P1 issues (semantic vs. episodic, emotional decay, contradiction detection)
3. **Week 3**: Add explanation layer and temporal clustering
4. **Week 4**: Implement identity/narrative layers (P2)
5. **Week 5+**: Nice-to-have features (P3)

