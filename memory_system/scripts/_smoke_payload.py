#!/usr/bin/env python3
"""端到端 smoke：5 轮注入 → 等 extract → 3 个 probe，断言关键词命中。"""
import json, sys, time, uuid, urllib.request

BASE = "http://127.0.0.1:8010"
OWNER = str(uuid.uuid4())
SID = f"smoke-{OWNER}"
USER_NAME = "用户"


def chat(user_msg, assistant_msg, session_id):
    body = json.dumps({
        "owner_id": OWNER,
        "user_name": USER_NAME,
        "session_id": session_id,
        "user_message": user_msg,
        "assistant_message": assistant_msg,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/v1/memory/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


print(f"OWNER={OWNER}")
turns = [
    ("我叫小红，今年8岁，在杭州西湖小学读三年级", "你好小红，很高兴认识你"),
    ("我喜欢画画和跳舞", "画画和跳舞都很有趣呢"),
    ("周末爸爸经常带我去爬山", "爬山是很好的运动"),
    ("我最讨厌吃胡萝卜", "试试新做法吧"),
    ("昨天我在学校得了一个小红花", "好棒呀，继续加油"),
]
print("=== inject ===")
for u, a in turns:
    d = chat(u, a, SID)
    rc = d.get("recall") or {}
    print(f"  intent={rc.get('intent')} latency={rc.get('latency_ms')}ms events={len(rc.get('events',[]))} person_id={d.get('person_id')!r}")
    time.sleep(1)

print("=== wait 40s for extract ===")
time.sleep(40)

print("=== probes ===")
probes = [
    ("我叫什么名字", ["小红"]),
    ("我喜欢做什么", ["画画", "跳舞"]),
    ("我讨厌吃什么", ["胡萝卜"]),
    ("周末做什么", ["爬山"]),
]
fails = 0
for q, kws in probes:
    d = chat(q, "", f"probe-{OWNER}")
    rc = d.get("recall") or {}
    profile = rc.get("profile_summary") or ""
    events = rc.get("events") or []
    sctx = rc.get("session_context") or []
    haystack = profile + " " + json.dumps(events, ensure_ascii=False) + " " + json.dumps(sctx, ensure_ascii=False)
    hit = any(kw in haystack for kw in kws)
    status = "OK" if hit else "FAIL"
    if not hit:
        fails += 1
    print(f"--- {q} -> {status} (looking for {kws})")
    print(f"    events={len(events)} intent={rc.get('intent')} sctx={len(sctx)}")
    print(f"    profile={profile!r}")

print(f"OWNER_FOR_DB={OWNER}")
print(f"=== summary: {len(probes)-fails}/{len(probes)} passed ===")
sys.exit(0 if fails == 0 else 1)
