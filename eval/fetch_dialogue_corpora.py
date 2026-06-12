"""公开多轮对话语料下载器（本沙箱已验证全部可达，2026-06）。

HuggingFace 在本环境被网络策略拦截；以下语料全部走 GitHub codeload，
仓库内直接带数据，无需表单/网盘。下载至 eval/data/corpora/（已 gitignore）。

用法：python eval/fetch_dialogue_corpora.py [名称…]   # 不带参数=全部
"""

from __future__ import annotations

import io
import sys
import tarfile
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parent / "data" / "corpora"

# 名称: (repo, 分支, 摘要)
CORPORA = {
    "cped": ("scutcyr/CPED", "main",
             "1.2万段电视剧多轮对话/9.4万句；每句情绪+对话动作+说话人大五人格/年龄/性别/场景 → 感知层校准"),
    "smile": ("qiuhuachuan/smile", "main",
              "5.5万段多轮心理支持对话（PsyQA改写）→ EQ确认/共情语料、distress场景挖掘"),
    "charactereval": ("morecry/CharacterEval", "main",
                      "4564条中文角色扮演多轮（含人物profile与12维人工评分体系）→ 人设一致性对标"),
    "kdconv": ("thu-coai/KdConv", "master",
               "4.5K段知识驱动多轮（影/乐/旅）→ 话题线头与深挖测试"),
    "cdconv": ("thu-coai/CDConv", "main",
               "对话矛盾检测集（含矛盾构造方法标注）→ 自述账本/人设不崩的对抗测试"),
    "esconv": ("thu-coai/Emotional-Support-Conversation", "main",
               "1.3K段英文情感支持多轮+策略标签 → 支持策略映射参考"),
}


def fetch(name: str) -> None:
    repo, branch, desc = CORPORA[name]
    out = DEST / name
    if out.exists():
        print(f"✓ {name} 已存在，跳过（{desc[:30]}…）")
        return
    url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}"
    print(f"下载 {name} ← {repo} …")
    buf = io.BytesIO(urllib.request.urlopen(url, timeout=300).read())
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        members = [
            m for m in tf.getmembers()
            if m.name.lower().endswith((".json", ".jsonl", ".csv", ".tsv", ".txt"))
            and "/codes/" not in m.name and not m.name.endswith("requirements.txt")
        ]
        for m in members:
            m.name = "/".join(m.name.split("/")[1:])   # 去掉仓库根目录前缀
            if m.name:
                tf.extract(m, out)
    print(f"  → {out}（{sum(1 for _ in out.rglob('*') if _.is_file())} 个数据文件）  {desc}")


def main() -> None:
    names = [a for a in sys.argv[1:] if a in CORPORA] or list(CORPORA)
    for n in names:
        fetch(n)


if __name__ == "__main__":
    main()
