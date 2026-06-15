"""生成 relationshape 营销/汇报 PPT（16:9，深色高级风，含图表）。

设计系统：藏青基底 + 暖珊瑚/琥珀 + 冷青；高对比、暖主调；统一栅格、标题、页脚、强调件。
运行：先 python deck/make_charts.py，再 python deck/make_deck.py
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ASSETS = Path(__file__).resolve().parent / "assets"
OUT = Path(__file__).resolve().parent / "relationshape_概览.pptx"

# ── 调色板 ──
INK = RGBColor.from_string("0E1530")
INK2 = RGBColor.from_string("141C3A")
CARD = RGBColor.from_string("1B2547")
LINE = RGBColor.from_string("33406A")
WHITE = RGBColor.from_string("F4F1EA")
MUTED = RGBColor.from_string("9AA6C4")
CORAL = RGBColor.from_string("FF7A66")
TEAL = RGBColor.from_string("4FD1C5")
AMBER = RGBColor.from_string("FFC75F")
GREEN = RGBColor.from_string("5BD6A0")

FONT = "Microsoft YaHei"      # CJK 字体；缺失时查看器自动回退到系统中文字体
SW, SH = 13.333, 7.5
MX = 0.85                      # 左右页边

prs = Presentation()
prs.slide_width = Inches(SW)
prs.slide_height = Inches(SH)


def _set_font(run, name=FONT):
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", name)


def slide():
    s = prs.slides.add_slide(prs.slide_layouts[6])
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    r.fill.solid(); r.fill.fore_color.rgb = INK
    r.line.fill.background(); r.shadow.inherit = False
    return s


def rect(s, x, y, w, h, fill=None, line=None, lw=1.0, rounded=False, shadow=False):
    shp = s.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid(); shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line; shp.line.width = Pt(lw)
    shp.shadow.inherit = False
    if rounded:
        try:
            shp.adjustments[0] = 0.06
        except Exception:
            pass
    return shp


def text(s, x, y, w, h, paras, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame; tf.word_wrap = True; tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = para.get("align", align)
        p.space_after = Pt(para.get("after", 0)); p.space_before = Pt(para.get("before", 0))
        p.line_spacing = para.get("line", 1.0)
        for r in para["runs"]:
            run = p.add_run(); run.text = r[0]
            run.font.size = Pt(r[1]); run.font.color.rgb = r[2]
            run.font.bold = r[3] if len(r) > 3 else False
            _set_font(run)
    return tb


def pic(s, name, x, y, w):
    p = s.shapes.add_picture(str(ASSETS / name), Inches(x), Inches(y), width=Inches(w))
    return p


def kicker_title(s, kicker, title, tcolor=WHITE):
    rect(s, MX, 0.62, 0.09, 0.52, fill=CORAL)              # 标题旁强调竖条
    text(s, MX + 0.22, 0.5, SW - 2*MX, 0.4,
         [{"runs": [(kicker, 13, TEAL, True)]}])
    text(s, MX + 0.2, 0.78, SW - 2*MX, 0.7,
         [{"runs": [(title, 30, tcolor, True)]}])


def footer(s, n):
    text(s, MX, SH - 0.5, 4, 0.3, [{"runs": [("relationshape", 11, MUTED, True)]}])
    text(s, SW - MX - 2, SH - 0.5, 2, 0.3,
         [{"runs": [(f"{n:02d}", 11, MUTED, False)]}], align=PP_ALIGN.RIGHT)


def card(s, x, y, w, h, title, body, accent=TEAL, big=None):
    rect(s, x, y, w, h, fill=CARD, rounded=True)
    rect(s, x, y, 0.07, h, fill=accent)                    # 左侧色条
    yy = y + 0.28
    if big:
        text(s, x + 0.3, yy, w - 0.5, 0.9, [{"runs": [(big, 34, accent, True)]}])
        yy += 0.82
    text(s, x + 0.3, yy, w - 0.55, 0.5, [{"runs": [(title, 16, WHITE, True)]}])
    if body:
        text(s, x + 0.3, yy + 0.5, w - 0.55, h - (yy - y) - 0.5,
             [{"runs": [(body, 12.5, MUTED, False)], "line": 1.18}])


def chip(s, x, y, w, big, label, color):
    rect(s, x, y, w, 1.5, fill=INK2, line=LINE, lw=1, rounded=True)
    text(s, x, y + 0.24, w, 0.7, [{"runs": [(big, 30, color, True)]}], align=PP_ALIGN.CENTER)
    text(s, x, y + 1.0, w, 0.4, [{"runs": [(label, 12, MUTED, False)]}], align=PP_ALIGN.CENTER)


# ════════════════════════════ 01 封面 ════════════════════════════
s = slide()
rect(s, 0, 0, 0.28, SH, fill=CORAL)
rect(s, 0.28, 0, 0.07, SH, fill=AMBER)
text(s, MX + 0.2, 1.7, SW - 2, 0.5, [{"runs": [("陪伴型 AI 的关系内核", 16, TEAL, True)]}])
text(s, MX + 0.16, 2.15, SW - 1.5, 1.5,
     [{"runs": [("relationshape", 60, WHITE, True)]}])
text(s, MX + 0.2, 3.35, SW - 3, 1.0,
     [{"runs": [("随时间生长的 ", 26, WHITE, False), ("关系", 26, CORAL, True),
                (" · ", 26, MUTED, False), ("人格", 26, AMBER, True),
                (" · ", 26, MUTED, False), ("情商", 26, TEAL, True), (" 引擎", 26, WHITE, False)]}])
text(s, MX + 0.2, 4.2, SW - 3.5, 0.6,
     [{"runs": [("为长期陪伴型 AI，提供每一轮对话的结构化指令——挂在任意大模型旁边。", 15, MUTED, False)]}])
chip(s, MX + 0.2, 5.2, 3.4, "100 万轮", "独立对话 · 零记忆污染", GREEN)
chip(s, MX + 0.2 + 3.7, 5.2, 3.4, "1.6 ms", "关键路径召回延迟", TEAL)
chip(s, MX + 0.2 + 7.4, 5.2, 3.4, "零依赖", "纯标准库内核", AMBER)
footer(s, 1)

# ════════════════════════════ 02 问题 ════════════════════════════
s = slide()
kicker_title(s, "PROBLEM · 痛点", "大模型很会说话，但没有「和你的关系」")
text(s, MX, 1.75, SW - 2*MX, 0.9,
     [{"runs": [("把人设写成长提示词，得到的是一个永远热情、永远讨好、转头就忘、对谁都一样的客服。",
                 15, MUTED, False)], "line": 1.3}])
cards = [("转头就忘", "说过的名字、喜好、约定，下一轮就消失；记忆靠字面命中，问句、闲聊还会污染。", CORAL),
         ("对谁都一样", "哥哥、妹妹共用一个人格；没有专属的相处方式、内部梗、磨合出的语气。", AMBER),
         ("永远在讨好", "不会成长、不分阶段、没有自尊与边界；热情是设定，不是关系。", TEAL)]
cw = (SW - 2*MX - 0.6) / 3
for i, (t, b, c) in enumerate(cards):
    card(s, MX + i*(cw+0.3), 2.9, cw, 3.2, t, b, c)
footer(s, 2)

# ════════════════════════════ 03 方案 ════════════════════════════
s = slide()
kicker_title(s, "SOLUTION · 方案", "把关系做成可生长、可持久化、可测试的状态机")
flow = ["用户说话", "prepare_turn", "TurnDirective\n本轮指令", "注入任意大模型", "生成回复"]
fcol = [MUTED, TEAL, CORAL, MUTED, MUTED]
bw, bh, gap = 2.05, 1.1, 0.28
x0 = MX + 0.1; yb = 2.5
for i, (t, c) in enumerate(zip(flow, fcol)):
    x = x0 + i*(bw+gap)
    rect(s, x, yb, bw, bh, fill=CARD, line=(c if c != MUTED else LINE), lw=1.5, rounded=True)
    text(s, x, yb, bw, bh, [{"runs": [(t, 13.5, WHITE, True)], "line": 1.05}],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    if i < len(flow)-1:
        text(s, x+bw, yb, gap, bh, [{"runs": [("›", 22, AMBER, True)]}],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
text(s, x0, yb+bh+0.25, SW-2*MX, 0.5,
     [{"runs": [("⟲  ", 15, AMBER, True), ("commit：记忆写入 · 信任记账 · 人格适应 · 阶段推进 · 承诺追踪——关系向前生长一步",
                 13.5, MUTED, False)]}])
rect(s, MX, 4.85, SW-2*MX, 1.5, fill=INK2, line=LINE, lw=1, rounded=True)
text(s, MX+0.35, 5.05, SW-2*MX-0.7, 1.2,
     [{"runs": [("引擎自己不调用大模型、零外部依赖。", 16, WHITE, True)], "after": 6},
      {"runs": [("它决定「以什么身份、什么情绪、什么形状、什么记忆去回应」；大模型只负责把结构化要求说成人话。",
                 14, MUTED, False)], "line": 1.25}])
footer(s, 3)

# ════════════════════════════ 04 关系生长 ════════════════════════════
s = slide()
kicker_title(s, "GROWTH · 随时间生长", "一段关系长什么样：30 天的完整生长")
pic(s, "growth.png", MX, 1.75, 7.7)
gx = MX + 8.05
notes = [("第 0 天 · 陌生", "礼貌克制，记下「喜欢恐龙」，轻确认一次", CORAL),
         ("第 12 天 · 裂痕-修复", "被骂不服气、守住自尊；安抚后信任反而更高", AMBER),
         ("第 15 天 · 熟悉", "开场主动惦记「上次你说有点难过，好点了吗」", TEAL),
         ("第 30 天 · 重逢", "隔 8 天重逢，暖场但零指责", GREEN)]
for i, (t, b, c) in enumerate(notes):
    yy = 1.95 + i*1.15
    rect(s, gx, yy, 0.06, 0.95, fill=c)
    text(s, gx+0.2, yy, 3.6, 0.4, [{"runs": [(t, 14, WHITE, True)]}])
    text(s, gx+0.2, yy+0.38, 3.6, 0.6, [{"runs": [(b, 11.5, MUTED, False)], "line": 1.12}])
footer(s, 4)

# ════════════════════════════ 05 核心能力 ════════════════════════════
s = slide()
kicker_title(s, "CAPABILITIES · 核心能力", "五个层次，一套引擎")
caps = [("关系生长", "陌生→相识→熟悉→同伴→知己；时间×互动×信任三重门槛", CORAL),
        ("双层人格", "气质/价值观/边界永不变；语气/幽默/内部梗随这位用户演进", AMBER),
        ("记忆系统", "情景/语义记忆、遗忘曲线、复习强化、承诺生命周期", TEAL),
        ("情商层", "确认六级·情绪粒度·知觉检核等十个机制，皆有文献根基", GREEN),
        ("安全红线", "危机接管、不制造依赖、每用户隔离、钩子防污染", CORAL)]
cw = (SW - 2*MX - 4*0.25) / 5
for i, (t, b, c) in enumerate(caps):
    card(s, MX + i*(cw+0.25), 2.0, cw, 3.9, t, b, c)
footer(s, 5)

# ════════════════════════════ 06 理论根基 ════════════════════════════
s = slide()
kicker_title(s, "FOUNDATIONS · 理论根基", "理论驱动，不是拍脑袋")
th = [("Knapp 阶段模型", "关系分阶段生长"), ("社会渗透理论", "表露深度是亲密度的货币"),
      ("Gottman 情感邀请 / 四骑士", "回应方式决定关系走向"), ("裂痕-修复", "修好的冲突反而加深信任"),
      ("OCC 评估理论", "情绪=事件对目标的意义"), ("PAD 心境 + 大五人格", "情绪是脉冲，心境是背景"),
      ("Mayer-Salovey 四分支", "感知→理解→运用→管理"), ("主动倾听 / 动机式访谈", "复述→确认→先问后建议"),
      ("Brown & Levinson 礼貌", "按阶段调节礼貌策略"), ("良性冒犯 + Martin 幽默", "好笑=越界×无害，只用健康象限"),
      ("峰终定律 + 蔡格尼克", "暖收尾 + 留个明天的钩子"), ("艾宾浩斯遗忘曲线", "记忆按半衰期衰减，召回延寿")]
cw = (SW - 2*MX - 2*0.3) / 3
ch = 1.18
for i, (t, b) in enumerate(th):
    r, cidx = divmod(i, 3)
    x = MX + cidx*(cw+0.3); y = 1.95 + r*(ch+0.18)
    rect(s, x, y, cw, ch, fill=CARD, rounded=True)
    rect(s, x, y, 0.06, ch, fill=[CORAL, AMBER, TEAL, GREEN][cidx % 4])
    text(s, x+0.25, y+0.18, cw-0.4, 0.4, [{"runs": [(t, 13.5, WHITE, True)]}])
    text(s, x+0.25, y+0.6, cw-0.4, 0.5, [{"runs": [(b, 11, MUTED, False)], "line": 1.1}])
text(s, MX, SH-0.95, SW-2*MX, 0.4,
     [{"runs": [("完整文献库与机制提炼见 docs/EQ_CANON.md", 11.5, MUTED, False)]}])
footer(s, 6)

# ════════════════════════════ 07 双层人格 ════════════════════════════
s = slide()
kicker_title(s, "PERSONALITY · 双层人格", "随用户演进，但不变成另一个人")
half = (SW - 2*MX - 0.5) / 2
rect(s, MX, 2.0, half, 4.0, fill=CARD, rounded=True)
rect(s, MX, 2.0, half, 0.6, fill=CORAL, rounded=True)
text(s, MX+0.35, 2.1, half-0.7, 0.45, [{"runs": [("内核 · 永不改变", 17, INK, True)]}], anchor=MSO_ANCHOR.MIDDLE)
for i, t in enumerate(["大五气质基线", "价值观与原则", "边界与自尊下限", "危机响应红线"]):
    text(s, MX+0.4, 2.95+i*0.72, half-0.8, 0.5,
         [{"runs": [("◆  ", 13, CORAL, True), (t, 15, WHITE, False)]}])
rect(s, MX+half+0.5, 2.0, half, 4.0, fill=CARD, rounded=True)
rect(s, MX+half+0.5, 2.0, half, 0.6, fill=TEAL, rounded=True)
text(s, MX+half+0.85, 2.1, half-0.7, 0.45, [{"runs": [("表达层 · 随这位用户演进", 17, INK, True)]}], anchor=MSO_ANCHOR.MIDDLE)
for i, t in enumerate(["语气正式度 / 能量趋同", "幽默偏好与吃哪套梗", "称呼 · 你们的内部梗", "相处教训与磨合记忆"]):
    text(s, MX+half+0.9, 2.95+i*0.72, half-0.8, 0.5,
         [{"runs": [("◆  ", 13, TEAL, True), (t, 15, WHITE, False)]}])
text(s, MX, 6.25, SW-2*MX, 0.5,
     [{"runs": [("一个朋友会和你磨合出独有的相处方式，但不会换一个人格——每个用户一份表达层，按 user_id 隔离持久化。",
                 13, MUTED, False)]}], align=PP_ALIGN.CENTER)
footer(s, 7)

# ════════════════════════════ 08 安全 ════════════════════════════
s = slide()
kicker_title(s, "SAFETY · 安全红线", "安全门先于一切人格逻辑")
safe = [("危机接管", "家暴/自伤/侵害命中即接管：稳稳接住 + 这不是你的错 + 指向信任的大人，且不承诺保密；此类记忆封存，永不被闲聊召回。", CORAL),
        ("不制造依赖", "内在恐惧只参与状态演化，永不渲染进提示词；重逢零指责，被拒绝就收住，奖励不用变率强化。", AMBER),
        ("每用户隔离", "心境/信任/记忆/适应全部按 user_id 分文件原子持久化；哥哥骂了它，不会带着委屈跟妹妹说话。", TEAL),
        ("钩子防污染", "只有用户的真实正向话题能成为下一轮线头；对角色的攻击/夸奖、设备抱怨进账本不进情景记忆。", GREEN)]
cw = (SW - 2*MX - 0.4) / 2
ch = 1.95
for i, (t, b, c) in enumerate(safe):
    r, cidx = divmod(i, 2)
    card(s, MX + cidx*(cw+0.4), 1.95 + r*(ch+0.25), cw, ch, t, b, c)
footer(s, 8)

# ════════════════════════════ 09 记忆方法论 ════════════════════════════
s = slide()
kicker_title(s, "METHODOLOGY · 评测方法论", "记忆系统：精确率优先 + 反作弊评测")
steps = [("生成式留出集", "槽位池按奇偶切 dev/test，填充词不重叠；模板×槽位组出真·上万条互不相同的用例", CORAL),
         ("先跑基线找盲区", "用失败样本定位真实句式盲区，而不是对着自己的考卷复习", AMBER),
         ("修通用机制", "改的是模式类/泛化规则，绝不写测试数据进代码、不用特例自欺", TEAL),
         ("验证 dev≈test", "开发集与留出集分数贴合，证明没有过拟合", GREEN)]
cw = (SW - 2*MX - 3*0.25) / 4
for i, (t, b, c) in enumerate(steps):
    card(s, MX + i*(cw+0.25), 1.95, cw, 2.7, f"{i+1}. {t}", b, c)
rect(s, MX, 5.0, SW-2*MX, 1.35, fill=INK2, line=LINE, lw=1, rounded=True)
text(s, MX+0.35, 5.18, SW-2*MX-0.7, 1.1,
     [{"runs": [("核心原则", 14, AMBER, True),
                ("：不能把测试数据写进代码，不能用特例自认为解决问题，要做高层抽象的泛化管理。",
                 14, WHITE, False)], "after": 5},
      {"runs": [("子串接地：接入 LLM 抽取时，抽出的值必须是原话子串——模型只能框选/归一，凭空造的词污染不进记忆。",
                 12.5, MUTED, False)], "line": 1.2}])
footer(s, 9)

# ════════════════════════════ 10 精确率 ════════════════════════════
s = slide()
kicker_title(s, "METRICS · 精确率", "100 万轮独立对话 · 零记忆污染")
pic(s, "precision.png", MX, 1.9, 7.5)
gx = MX + 7.9
chip(s, gx, 2.0, 4.1, "1,000,000", "轮独立多轮对话（10万组×10轮）", GREEN)
chip(s, gx, 3.7, 4.1, "100%", "五项强不变量全部满分", TEAL)
text(s, gx, 5.45, 4.1, 1.6,
     [{"runs": [("全新词表 / 句式 / 结构，与开发集零共享；换种子复测一致。", 12.5, MUTED, False)], "line": 1.25, "after": 6},
      {"runs": [("第三方喜好不误记 · 问句闲聊不学 · 撤回必生效。", 12.5, WHITE, False)], "line": 1.25}])
footer(s, 10)

# ════════════════════════════ 11 召回率 ════════════════════════════
s = slide()
kicker_title(s, "METRICS · 召回率", "规则层已追平「完美 LLM 上界」")
pic(s, "recall_improvement.png", MX, 1.9, 7.6)
gx = MX + 8.0
for i, (t, b, c) in enumerate([
        ("名字 / 喜好 / 厌恶 / 朋友", "纯规则召回全部 100%，无需 LLM 兜底", TEAL),
        ("精确率注入 · 10 万", "生产日志 5 类污染，100% 拦住", GREEN),
        ("标准召回 · 5 万留出", "99% 泛化召回基线", AMBER),
        ("冲突更新 / 带属性实体", "撤回·更替·转移 / 按属性检索 100%", CORAL)]):
    yy = 2.0 + i*1.08
    rect(s, gx, yy, 0.06, 0.9, fill=c)
    text(s, gx+0.2, yy, 3.7, 0.4, [{"runs": [(t, 13.5, WHITE, True)]}])
    text(s, gx+0.2, yy+0.36, 3.7, 0.5, [{"runs": [(b, 11, MUTED, False)], "line": 1.1}])
footer(s, 11)

# ════════════════════════════ 12 召回速度 ════════════════════════════
s = slide()
kicker_title(s, "METRICS · 召回速度", "满载也只 1.4 毫秒")
pic(s, "recall_speed.png", MX, 1.9, 7.6)
gx = MX + 8.0
chip(s, gx, 2.0, 3.9, "1.4 ms", "满载 400 条情景 · 单次召回", TEAL)
chip(s, gx, 3.7, 3.9, "1.6 ms", "端到端 prepare_turn @400", GREEN)
text(s, gx, 5.45, 3.95, 1.6,
     [{"runs": [("O(n) 随记忆量线性，400 上限封顶。", 12.5, WHITE, False)], "line": 1.25, "after": 6},
      {"runs": [("相对大模型首字（数百毫秒）可忽略；单核 700+ 次/秒。", 12.5, MUTED, False)], "line": 1.25}])
footer(s, 12)

# ════════════════════════════ 13 测试总览 ════════════════════════════
s = slide()
kicker_title(s, "QUALITY · 测试总览", "一键跑齐 · 全绿")
pic(s, "test_overview.png", MX, 1.85, 8.0)
gx = MX + 8.4
chip(s, gx, 2.1, 3.7, "5 / 5", "强校验全部通过", GREEN)
chip(s, gx, 3.8, 3.7, "234", "项自动化测试", TEAL)
text(s, gx, 5.55, 3.75, 1.4,
     [{"runs": [("单测 + 压测 + 模糊 + 快照 + 持久化，", 12.5, MUTED, False),
                ("一条命令出总表。", 12.5, WHITE, True)], "line": 1.25}])
footer(s, 13)

# ════════════════════════════ 14 LLM 抽取层 ════════════════════════════
s = slide()
kicker_title(s, "EXTENSIBLE · 可选增强", "LLM 抽取层：补召回，不破精确率")
left = (SW - 2*MX - 0.5)/2
card(s, MX, 2.0, left, 3.9, "默认：纯规则",
     "零外部依赖、确定性、精确率满分。覆盖常见关键词句式，毫秒级。", TEAL)
card(s, MX+left+0.5, 2.0, left, 3.9, "接入：规则 + LLM",
     "把上层大模型实成 MemoryExtractorPort，补齐口语/语义句式，召回推到上界。", CORAL)
rect(s, MX, 6.05, SW-2*MX, 0.9, fill=INK2, line=AMBER, lw=1.2, rounded=True)
text(s, MX+0.35, 6.05, SW-2*MX-0.7, 0.9,
     [{"runs": [("子串接地  ", 14, AMBER, True),
                ("LLM 只能框选/归一原文片段，凭空造的词进不了记忆——精确率不变量在 LLM 介入后依然成立。",
                 13, WHITE, False)]}], anchor=MSO_ANCHOR.MIDDLE)
footer(s, 14)

# ════════════════════════════ 15 持久化 ════════════════════════════
s = slide()
kicker_title(s, "PERSISTENCE · 持久化", "从 JSON 到真实数据库")
left = (SW - 2*MX - 0.5)/2
card(s, MX, 2.0, left, 3.7, "JSON · 默认",
     "每用户一份状态文件，原子写入，零依赖。开箱即用，适合单机与起步。", TEAL)
card(s, MX+left+0.5, 2.0, left, 3.7, "PostgreSQL · 生产",
     "复用 memory_system 的 PG 实例；状态整存 JSONB——事务/并发安全、可按 JSON 路径查询、随版本演进零迁移。", CORAL)
text(s, MX, 5.95, SW-2*MX, 0.6,
     [{"runs": [("同一 StateStore 接口可插拔，配置即切换；跨进程恢复、精确率跨持久化保持，已在真实 PG 上端到端验证。",
                 13, MUTED, False)]}], align=PP_ALIGN.CENTER)
footer(s, 15)

# ════════════════════════════ 16 工程严谨 ════════════════════════════
s = slide()
kicker_title(s, "ENGINEERING · 工程严谨", "可测试 · 可持久化 · 可观测")
stats = [("234", "项自动化测试", TEAL), ("零", "外部依赖内核（纯标准库）", AMBER),
         ("54 轮", "指令快照零漂移回归", CORAL), ("上万条", "生成式留出集 · 反作弊", GREEN)]
cw = (SW - 2*MX - 3*0.3)/4
for i, (b, l, c) in enumerate(stats):
    x = MX + i*(cw+0.3)
    rect(s, x, 2.2, cw, 2.3, fill=CARD, rounded=True)
    rect(s, x, 2.2, cw, 0.07, fill=c)
    text(s, x, 2.65, cw, 0.9, [{"runs": [(b, 36, c, True)]}], align=PP_ALIGN.CENTER)
    text(s, x+0.2, 3.65, cw-0.4, 0.7, [{"runs": [(l, 12.5, MUTED, False)], "line": 1.15}], align=PP_ALIGN.CENTER)
text(s, MX, 5.1, SW-2*MX, 1.0,
     [{"runs": [("感知层是稳定契约，可整体替换为分类模型/LLM；记忆融合留有 MemoryPort 座椅——", 13.5, WHITE, False)], "after": 5},
      {"runs": [("结构不变，能力可演进。", 13.5, MUTED, False)]}], align=PP_ALIGN.CENTER)
footer(s, 16)

# ════════════════════════════ 17 营销卖点 ════════════════════════════
s = slide()
kicker_title(s, "WHY · 为什么选它", "不是更大的模型，是更长的关系")
sells = [("记得准，不记错", "100 万轮独立对话零污染——结构化事实记忆稳定，比字面命中可靠。", CORAL),
         ("随时间生长", "陌生到知己，每个孩子磨合出一个独有的它，而不是统一客服。", AMBER),
         ("理论驱动 · 安全优先", "十余套人际/情绪/幽默文献落地为机制；危机接管与不制造依赖是红线。", TEAL),
         ("轻到可忽略", "零依赖内核，关键路径 1.6ms，挂在任意大模型旁边即用。", GREEN)]
cw = (SW - 2*MX - 0.4)/2
ch = 1.95
for i, (t, b, c) in enumerate(sells):
    r, cidx = divmod(i, 2)
    card(s, MX + cidx*(cw+0.4), 1.95 + r*(ch+0.25), cw, ch, t, b, c)
footer(s, 17)

# ════════════════════════════ 18 结尾 ════════════════════════════
s = slide()
rect(s, 0, 0, SW, SH, fill=INK)
rect(s, 0, SH-0.35, SW, 0.12, fill=CORAL)
rect(s, 0, SH-0.23, SW, 0.06, fill=AMBER)
text(s, 0, 2.5, SW, 1.2, [{"runs": [("relationshape", 54, WHITE, True)]}], align=PP_ALIGN.CENTER)
text(s, 0, 3.75, SW, 0.7,
     [{"runs": [("给长期陪伴型 AI，一段真正会生长的关系。", 20, TEAL, False)]}], align=PP_ALIGN.CENTER)
text(s, 0, 4.7, SW, 0.5,
     [{"runs": [("关系 · 人格 · 情商 · 记忆 · 安全 —— 一套可生长、可持久化、可测试的状态机", 13, MUTED, False)]}],
     align=PP_ALIGN.CENTER)
footer(s, 18)

prs.save(str(OUT))
print("saved:", OUT, f"({len(prs.slides.__iter__.__self__._sldIdLst)} slides)")
