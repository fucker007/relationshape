# Implementation Roadmap — Critical Fixes

## Phase 1: P0 Issues (System Breaking) — Week 1

### 1.1 Vector Search Fallback Mechanism

**File**: `retrieval/recall.py`

**Current State**:
```python
async def recall(person_id, context, redis, pg, limit, types, min_importance):
    query_embedding = await embed_text(context)
    if not query_embedding:
        return [], {}, "empty"

    vector_results = await pg.vector_search_events(...)
    # No fallback if results are poor
```

**Problem**: If embeddings fail or return low-confidence results, system returns nothing.

**Fix**:
```python
async def recall(person_id, context, redis, pg, limit, types, min_importance):
    query_embedding = await embed_text(context)

    # Primary: vector search
    vector_results = await pg.vector_search_events(
        owner_id=person_id,
        query_embedding=query_embedding,
        limit=limit * 3,
    )

    # Compute confidence
    scores = [r.get("final_score", 0.0) for r in vector_results]
    confidence = _compute_confidence(scores)

    # Fallback: if confidence is low, try keyword search
    if confidence in ("low", "empty"):
        keyword_results = await _keyword_search_fallback(
            redis, person_id, context, limit
        )
        if keyword_results:
            vector_results = keyword_results
            confidence = "uncertain"  # lower confidence for keyword search

    # Filter and score
    combined = []
    for item in vector_results:
        if types and item.get("event_type") not in types:
            continue
        importance = float(item.get("importance", 0.5))
        if importance < min_importance:
            continue

        item["final_score"] = (
            0.7 * float(item.get("similarity", 0))
            + 0.2 * importance
            + 0.1 * _recency_factor(item.get("event_time", ""))
        )
        combined.append(item)

    combined.sort(key=lambda x: x["final_score"], reverse=True)
    results = combined[:limit]

    sources = {"vector": len(results)}
    return results, sources, confidence


async def _keyword_search_fallback(redis, person_id, context, limit):
    """Fallback keyword search when vector search fails"""
    keywords = _extract_keywords(context)
    if not keywords:
        return []

    # Search Redis inverted index
    results = []
    for keyword in keywords:
        key = f"keyword_index:{person_id}:{keyword}"
        event_ids = await redis.smembers(key)
        results.extend(event_ids)

    # Deduplicate and fetch full events
    unique_ids = list(set(results))[:limit]
    events = await pg.get_events_by_ids(unique_ids)
    return events
```

**Testing**:
```python
# tests/unit/test_recall_fallback.py
async def test_vector_search_fallback():
    # Mock embed_text to return low-confidence embeddings
    # Verify keyword search is triggered
    # Verify results are returned
```

---

### 1.2 Optimistic Locking for PersonNode Updates

**File**: `storage/pg_store.py`

**Current State**:
```python
async def update_person_field(self, person_id, field, value):
    await self.pg.execute(
        f"UPDATE person_nodes SET {field}=$1 WHERE person_id=$2",
        value, person_id
    )
```

**Problem**: No version checking, concurrent updates can overwrite each other.

**Fix**:
```python
# Add version column to person_nodes table
# ALTER TABLE person_nodes ADD COLUMN version INT DEFAULT 0;

async def update_person_field(self, person_id, field, value, expected_version=None):
    """Update with optimistic locking"""
    if expected_version is None:
        # Get current version
        node = await self.get_person_node(person_id)
        expected_version = node.get("version", 0)

    result = await self.pg.execute(
        f"""UPDATE person_nodes
           SET {field}=$1, version=version+1
           WHERE person_id=$2 AND version=$3""",
        value, person_id, expected_version
    )

    if result.rowcount == 0:
        raise ConcurrencyError(
            f"Version mismatch for person {person_id}. "
            f"Expected version {expected_version}, but was updated by another process."
        )

    return expected_version + 1


async def update_person_node(self, person_id, updates):
    """Atomic update of multiple fields"""
    node = await self.get_person_node(person_id)
    current_version = node.get("version", 0)

    set_clauses = []
    values = []
    for field, value in updates.items():
        set_clauses.append(f"{field}=${ len(values) + 1}")
        values.append(value)

    values.extend([person_id, current_version])

    result = await self.pg.execute(
        f"""UPDATE person_nodes
           SET {', '.join(set_clauses)}, version=version+1
           WHERE person_id=${len(values)-1} AND version=${len(values)}""",
        *values
    )

    if result.rowcount == 0:
        raise ConcurrencyError(f"Concurrent update detected for person {person_id}")
```

**Testing**:
```python
# tests/unit/test_optimistic_locking.py
async def test_concurrent_updates():
    # Simulate two concurrent updates
    # Verify one succeeds and one fails with ConcurrencyError
    # Verify retry logic works
```

---

### 1.3 Centralized Cache Invalidation

**File**: `storage/redis_store.py`

**Current State**:
```python
# Scattered invalidation calls
await redis.invalidate_recall_cache(person_id)
# But other caches not invalidated
```

**Problem**: Incomplete invalidation leads to stale data.

**Fix**:
```python
class CacheInvalidator:
    """Centralized cache invalidation"""

    def __init__(self, redis: RedisStore):
        self.redis = redis

    async def invalidate_person(self, person_id: str):
        """Invalidate all caches for a person"""
        await asyncio.gather(
            self.redis.delete(f"profile:{person_id}"),
            self.redis.delete(f"recall_cache:{person_id}:*"),
            self.redis.delete(f"relationships:{person_id}:*"),
            self.redis.delete(f"daily_emotions:{person_id}:*"),
            self.redis.delete(f"focus:{person_id}"),
            self.redis.delete(f"keyword_index:{person_id}:*"),
        )

    async def invalidate_relationship(self, from_id: str, to_id: str):
        """Invalidate relationship caches"""
        await asyncio.gather(
            self.redis.delete(f"relationships:{from_id}:{to_id}"),
            self.redis.delete(f"relationships:{from_id}:*"),
        )

    async def invalidate_event(self, event_id: str, person_id: str):
        """Invalidate caches affected by event change"""
        await self.invalidate_person(person_id)
        # Also invalidate related persons
        event = await pg.get_event(event_id)
        for participant_id in event.get("participant_ids", []):
            await self.invalidate_person(str(participant_id))


# Usage in ProfileUpdater
class ProfileUpdater:
    def __init__(self, graph_store: GraphStore, cache_invalidator: CacheInvalidator):
        self._gs = graph_store
        self._cache = cache_invalidator

    async def update_from_attributes(self, owner_id, person_id, attributes):
        updates = []
        # ... perform updates ...

        # Invalidate all related caches
        await self._cache.invalidate_person(person_id)
        await self._cache.invalidate_person(owner_id)

        return updates
```

**Testing**:
```python
# tests/unit/test_cache_invalidation.py
async def test_cache_invalidation_completeness():
    # Update person node
    # Verify all cache keys are invalidated
    # Verify no stale data is served
```

---

### 1.4 Event Merge Audit Trail

**File**: `models/person_graph.py`

**Current State**:
```python
class Event(BaseModel):
    event_id: UUID
    # ... no merge history ...
```

**Problem**: Merged events are permanent, no way to audit or unmerge.

**Fix**:
```python
class MergeRecord(BaseModel):
    merged_with_event_id: UUID
    merge_confidence: float  # 0.0-1.0 from LLM
    merged_at: datetime
    merge_reason: str  # "same_activity", "plan_execution", etc.


class Event(BaseModel):
    event_id: UUID
    owner_id: UUID
    session_id: UUID

    # ... existing fields ...

    # Merge tracking
    merge_history: list[MergeRecord] = Field(default_factory=list)
    is_merged: bool = False
    merged_into_event_id: UUID | None = None

    # Audit
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_modified_by: str = ""  # "merge_worker", "user", etc.


# In pipeline/merge_worker.py
async def merge_events(event_a_id, event_b_id, confidence):
    event_a = await pg.get_event(event_a_id)
    event_b = await pg.get_event(event_b_id)

    # Create merged event
    merged = Event(
        summary=f"{event_a.summary} + {event_b.summary}",
        # ... other fields ...
        merge_history=[
            MergeRecord(
                merged_with_event_id=event_b_id,
                merge_confidence=confidence,
                merged_at=datetime.utcnow(),
                merge_reason="semantic_similarity",
            )
        ],
    )

    # Mark originals as merged
    event_a.is_merged = True
    event_a.merged_into_event_id = merged.event_id
    event_b.is_merged = True
    event_b.merged_into_event_id = merged.event_id

    await pg.insert_event(merged)
    await pg.update_event(event_a)
    await pg.update_event(event_b)


# Periodic audit
async def audit_low_confidence_merges():
    """Flag merges with low confidence for manual review"""
    events = await pg.query(
        "SELECT * FROM events WHERE merge_history IS NOT NULL"
    )

    for event in events:
        for merge in event.merge_history:
            if merge.merge_confidence < 0.7:
                await flag_for_manual_review(event.event_id, merge)
```

---

## Phase 2: P1 Issues (Major UX) — Week 2

### 2.1 Semantic vs. Episodic Memory Distinction

**File**: `retrieval/graph_recall.py`

**Current State**:
```python
async def graph_recall(gs, owner_id, primary_person_id, query):
    # All queries treated the same
    events = await vector_search(...)
    return events
```

**Problem**: Preference queries should return PersonNode attributes, not events.

**Fix**:
```python
async def graph_recall(gs, owner_id, primary_person_id, query):
    intent = classify_intent(query)

    if intent == "preference":
        # Semantic memory: direct lookup
        node = await gs.get_person_node(primary_person_id)
        prefs = node.get("preferences", [])
        return GraphRecallResult(
            profile_summary=f"喜欢：{', '.join(p['item'] for p in prefs)}",
            events=[],
            intent="preference",
            confidence="high" if prefs else "empty",
            source="profile",
        )

    elif intent == "identity":
        # Semantic memory: direct lookup
        node = await gs.get_person_node(primary_person_id)
        identity = node.get("identity", {})
        summary = f"基本信息：{identity}"
        return GraphRecallResult(
            profile_summary=summary,
            events=[],
            intent="identity",
            confidence="high" if identity else "empty",
            source="profile",
        )

    elif intent == "event":
        # Episodic memory: vector search
        events = await vector_search(gs, owner_id, query, limit=10)
        return GraphRecallResult(
            events=events,
            intent="event",
            confidence=_compute_confidence([e.get("final_score", 0) for e in events]),
            source="vector",
        )

    # ... other intents ...
```

---

### 2.2 Emotion-Aware Recency Decay

**File**: `retrieval/recall.py`

**Current State**:
```python
def _recency_factor(created_at_str):
    days_ago = (datetime.now(timezone.utc) - dt).days
    return 1.0 / (1.0 + days_ago * settings.recall_recency_decay)
```

**Problem**: All emotions decay equally.

**Fix**:
```python
def _recency_factor(created_at_str, event_type=None, emotion=None):
    """Emotion-aware recency decay"""
    try:
        if isinstance(created_at_str, datetime):
            dt = created_at_str.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        days_ago = (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 0.5

    # Determine decay rate based on event type and emotion
    decay_rate = settings.recall_recency_decay  # default 0.01

    if event_type == "conflict":
        decay_rate = 0.001  # 10x slower decay for conflicts
    elif event_type == "emotional" and emotion in ["生气", "难过", "害怕", "绝望"]:
        decay_rate = 0.001  # 10x slower decay for negative emotions
    elif event_type == "daily":
        decay_rate = 0.05  # 5x faster decay for daily events
    elif event_type == "achievement":
        decay_rate = 0.005  # 2x slower decay for achievements

    return 1.0 / (1.0 + days_ago * decay_rate)


# Usage in recall()
for item in vector_results:
    item["final_score"] = (
        0.7 * float(item.get("similarity", 0))
        + 0.2 * importance
        + 0.1 * _recency_factor(
            item.get("event_time", ""),
            event_type=item.get("event_type"),
            emotion=item.get("emotion_summary"),
        )
    )
```

---

### 2.3 Contradiction Detection

**File**: `pipeline/profile_updater.py`

**Current State**:
```python
# No contradiction detection
```

**Problem**: Conflicting preferences coexist.

**Fix**:
```python
class ContradictionDetector:
    async def detect_contradictions(self, person_id: str):
        """Detect contradictions in preferences/aversions"""
        node = await self._gs.get_person_node(person_id)

        contradictions = []
        prefs = {p["item"]: p for p in node.get("preferences", [])}
        avers = {a["item"]: a for a in node.get("aversions", [])}

        for item in prefs:
            if item in avers:
                contradictions.append({
                    "item": item,
                    "preference": prefs[item],
                    "aversion": avers[item],
                    "type": "preference_aversion_conflict",
                })

        return contradictions

    async def resolve_contradiction(self, person_id: str, item: str, resolution: str):
        """Resolve contradiction by choosing one"""
        node = await self._gs.get_person_node(person_id)

        if resolution == "prefer":
            node["aversions"] = [a for a in node.get("aversions", []) if a["item"] != item]
        elif resolution == "avert":
            node["preferences"] = [p for p in node.get("preferences", []) if p["item"] != item]

        await self._gs.update_person_node(person_id, node)


# Usage in recall
async def recall_with_contradiction_check(person_id, context, redis, pg):
    detector = ContradictionDetector(pg)
    contradictions = await detector.detect_contradictions(person_id)

    if contradictions:
        # Flag for LLM to ask user
        return {
            "contradictions": contradictions,
            "message": f"I noticed you said you like and dislike {contradictions[0]['item']}. Which is it?",
        }
```

---

### 2.4 Explanation Layer

**File**: `retrieval/summary.py`

**Current State**:
```python
def build_summary(display_name, records):
    # Just concatenates events
    return f"{display_name}最近：" + ", ".join(r["summary"] for r in records)
```

**Problem**: No explanation of why these events were recalled.

**Fix**:
```python
class ExplanationBuilder:
    async def build_explanation(self, query, records, person_id, gs):
        """Build explanation for why these events were recalled"""
        if not records:
            return "No recent events found."

        # Analyze patterns
        event_types = {}
        emotions = {}
        people = {}

        for r in records:
            event_type = r.get("event_type", "unknown")
            event_types[event_type] = event_types.get(event_type, 0) + 1

            emotion = r.get("emotion_summary")
            if emotion:
                emotions[emotion] = emotions.get(emotion, 0) + 1

            for person in r.get("participant_names", []):
                people[person] = people.get(person, 0) + 1

        # Build explanation
        parts = []

        # Dominant emotion
        if emotions:
            dominant = max(emotions, key=emotions.get)
            parts.append(f"你最近感到{dominant}")

        # Main activities
        if event_types:
            main_type = max(event_types, key=event_types.get)
            parts.append(f"主要是{main_type}相关的事")

        # Key people
        if people:
            top_people = sorted(people.items(), key=lambda x: x[1], reverse=True)[:2]
            people_str = "和".join(p[0] for p in top_people)
            parts.append(f"涉及{people_str}")

        return "，".join(parts)


# Usage
async def recall_memories(req, redis, pg):
    records, sources, confidence = await recall(...)

    explanation_builder = ExplanationBuilder()
    explanation = await explanation_builder.build_explanation(
        req.context, records, req.person_id, pg
    )

    return RecallResponse(
        memories=results,
        explanation=explanation,
        search_latency_ms=latency,
        sources=sources,
        confidence=confidence,
    )
```

---

## Phase 3: P2 Issues (Design Flaws) — Week 3-4

### 3.1 Identity vs. Behavior Separation

**File**: `models/person_graph.py`

**Current State**:
```python
class PersonNode(BaseModel):
    identity: dict  # {age, birthday, school, ...}
    personality: list  # [{trait, evidence, intensity}]
    # Conflated
```

**Problem**: Demographic facts mixed with evolving self-concept.

**Fix**:
```python
class Demographics(BaseModel):
    """Immutable demographic facts"""
    name: str
    age: int | None = None
    birthday: date | None = None
    school: str | None = None
    grade: str | None = None
    gender: str | None = None


class SelfPerception(BaseModel):
    """Evolving self-concept"""
    traits: list[{
        trait: str,
        confidence: float,  # 0.0-1.0
        evidence_count: int,
        first_observed: datetime,
        last_observed: datetime,
    }]
    values: list[{
        value: str,
        importance: float,
    }]
    aspirations: list[{
        goal: str,
        priority: float,
    }]


class PersonNode(BaseModel):
    person_id: UUID
    owner_id: UUID
    name: str
    role: Literal["primary", "secondary"]

    # Separated
    demographics: Demographics = Field(default_factory=Demographics)
    self_perception: SelfPerception = Field(default_factory=SelfPerception)

    # Behavioral patterns
    preferences: list[dict] = Field(default_factory=list)
    aversions: list[dict] = Field(default_factory=list)
    behaviors: list[dict] = Field(default_factory=list)

    # ... rest of fields ...
```

---

### 3.2 Narrative Layer

**File**: `models/person_graph.py` (new)

**Fix**:
```python
class Narrative(BaseModel):
    """Causal narrative connecting events"""
    narrative_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    title: str  # "Why I'm shy"
    theme: str  # "social_anxiety", "self_confidence", etc.

    events: list[UUID]  # ordered chain of event_ids
    causal_chain: list[{
        from_event_id: UUID,
        to_event_id: UUID,
        relation_type: Literal["caused", "triggered", "reinforced", "resolved"],
        explanation: str,
    }]

    resolution: str | None  # how it was resolved
    is_active: bool = True  # ongoing or resolved

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# Usage
async def build_narrative(gs, person_id, theme):
    """Build narrative for a theme"""
    events = await gs.query_events_by_theme(person_id, theme)

    # Build causal chain
    causal_chain = []
    for i in range(len(events) - 1):
        # Use LLM to determine causality
        relation = await determine_causality(events[i], events[i+1])
        causal_chain.append({
            "from_event_id": events[i].event_id,
            "to_event_id": events[i+1].event_id,
            "relation_type": relation,
        })

    narrative = Narrative(
        owner_id=person_id,
        title=f"Why I'm {theme}",
        theme=theme,
        events=[e.event_id for e in events],
        causal_chain=causal_chain,
    )

    return narrative
```

---

## Testing Strategy

### Unit Tests
- `tests/unit/test_recall_fallback.py` — Vector search fallback
- `tests/unit/test_optimistic_locking.py` — Concurrent updates
- `tests/unit/test_cache_invalidation.py` — Cache completeness
- `tests/unit/test_contradiction_detection.py` — Contradiction detection

### Integration Tests
- `tests/integration/test_profile_update_consistency.py` — Profile updates with caching
- `tests/integration/test_event_merge_audit.py` — Merge audit trail
- `tests/integration/test_semantic_vs_episodic.py` — Intent-based recall

### E2E Tests
- `tests/e2e/test_user_satisfaction.py` — Full workflow with explanations
- `tests/e2e/test_contradiction_resolution.py` — User resolves contradictions

---

## Rollout Plan

1. **Week 1**: Deploy P0 fixes to staging, run full test suite
2. **Week 2**: Deploy P0 to production, monitor for issues
3. **Week 3**: Deploy P1 fixes to staging
4. **Week 4**: Deploy P1 to production
5. **Week 5+**: Deploy P2 features incrementally

