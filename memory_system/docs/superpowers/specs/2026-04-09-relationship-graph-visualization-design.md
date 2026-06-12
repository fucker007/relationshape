# 关系图谱可视化设计文档

**日期**: 2026-04-09  
**项目**: Memory System - 关系图谱可视化  
**状态**: 已批准

---

## 1. 概述

### 1.1 背景

当前的Memory Viewer Dashboard (`/dashboard/`) 使用传统的列表和卡片视图展示人物、事件和关系数据。这种展示方式在查看复杂的人际关系网络时存在局限性，用户难以直观理解人物之间的连接和事件的关联。

### 1.2 目标

设计并实现一个基于图谱的可视化界面，以节点-连线的方式展示：
- 用户(Owner)作为独立的关系网络孤岛
- 每个用户的人物关系网络
- 人物之间通过事件建立的连接
- 支持交互式探索和信息钻取

### 1.3 核心设计原则

- **以用户为中心**: 每个Owner是独立的关系网络，互不干扰
- **渐进式信息展示**: 默认隐藏细节，通过交互逐层展开
- **性能优先**: 支持大规模数据(100+节点)的流畅渲染
- **美学与功能并重**: 深色主题，现代化视觉设计

---

## 2. 整体架构

### 2.1 页面结构

**新增路由**: `/dashboard/graph`

**布局组成**:
```
┌─────────────────────────────────────────────────────┐
│  🕸️ 关系图谱    [搜索框]      统计信息  [刷新]      │ ← 顶部工具栏
├─────────────────────────────────────────────────────┤
│                                                     │
│                                                     │
│                   Canvas 画布                       │
│              (全屏交互式图谱)                        │
│                                                     │
│                                                     │
├─────────────────────────────────────────────────────┤
│  💡 双击空白返回 | ESC返回 | 滚轮缩放               │ ← 操作提示
└─────────────────────────────────────────────────────┘
                                    ┌──────────────┐
                                    │  侧边信息面板  │ ← 点击节点时滑出
                                    │              │
                                    └──────────────┘
```

### 2.2 技术栈

- **前端框架**: 纯HTML + Tailwind CSS + Vanilla JavaScript
- **图形库**: D3.js v7 (仅用于力导向布局计算)
- **渲染引擎**: HTML5 Canvas (双层Canvas架构)
- **数据接口**: 复用现有 `/dashboard/api/*` 端点

### 2.3 文件组织

```
api/
├── routers/
│   └── dashboard.py          (新增 /graph 路由)
└── static/
    ├── dashboard.html        (保持不变)
    └── graph.html            (新增: 单文件包含所有代码)
```

---

## 3. 视图状态与数据模型

### 3.1 三层视图状态机

```
OWNER_LIST (第一层)
    ↓ 点击Owner节点
PERSON_GRAPH (第二层)
    ↓ 点击连线
EVENT_EXPANDED (第三层)
    ↓ 双击空白 / ESC
返回 OWNER_LIST
```

### 3.2 数据结构

**Owner节点** (第一层):
```javascript
{
  id: "owner_uuid",
  type: "owner",
  label: "Owner名称",
  personCount: 15,
  eventCount: 89,
  createdAt: "2025-03-15T10:30:00Z",
  x: 0, y: 0,              // D3计算的坐标
  radius: 30               // 动态计算: 20 + sqrt(personCount) * 3
}
```

**人物节点** (第二层):
```javascript
{
  id: "person_uuid",
  type: "person",
  name: "张三",
  role: "primary" | "secondary",
  eventCount: 12,
  relationshipCount: 5,
  x: 0, y: 0,
  radius: 25,              // primary: 30px, secondary: 20px
  color: "#3b82f6"         // 根据role着色
}
```

**关系边**:
```javascript
{
  id: "rel_uuid",
  source: "person_id_1",
  target: "person_id_2",
  eventCount: 5,
  sentiment: 0.7,          // -1.0 到 1.0
  intensity: 0.8,          // 0.0 到 1.0 (交互频率)
  relationType: "friend",
  events: [...]            // 点击时加载
}
```

**事件节点** (第三层展开):
```javascript
{
  id: "event_uuid",
  type: "event",
  summary: "一起打篮球",
  eventType: "social",
  eventTime: "2025-03-10",
  importance: 0.75,
  emotions: {...},
  x: 0, y: 0,              // 插入在两人之间
  shape: "diamond"         // 菱形区分人物节点
}
```

### 3.3 API数据流

**第一层加载**:
- `GET /dashboard/api/owners` → Owner列表
- `GET /dashboard/api/persons?owner_id={id}` → 每个Owner的人物数量
- `GET /dashboard/api/stats` → 全局统计

**第二层加载** (点击Owner):
- `GET /dashboard/api/person/{owner_primary_person_id}` → Owner详情
- 从 `relationships` 字段提取关系和相关人物

**第三层加载** (点击连线):
- 从关系对象的 `last_event_id` 追溯
- 或新增接口: `GET /dashboard/api/relationship-events?from={id}&to={id}`

---

## 4. 视觉设计规范

### 4.1 配色方案 (深色主题)

**背景**:
- 画布: `#0f172a` (slate-900)
- 工具栏: `rgba(15, 23, 42, 0.9)` (半透明)

**节点颜色**:
- Owner节点: `linear-gradient(135deg, #3b82f6, #8b5cf6)` (蓝紫渐变)
- Primary人物: `#3b82f6` (蓝色)
- Secondary人物: `#64748b` (灰色)
- 事件节点: `#f59e0b` (琥珀色)

**连线颜色** (根据sentiment映射):
- 正面 (0.3 ~ 1.0): `#10b981` → `#34d399` (绿色系)
- 中性 (-0.3 ~ 0.3): `#64748b` (灰色)
- 负面 (-1.0 ~ -0.3): `#ef4444` → `#f87171` (红色系)

**连线粗细** (根据intensity映射):
- 低强度 (0.0 ~ 0.3): 1px
- 中强度 (0.3 ~ 0.7): 2px
- 高强度 (0.7 ~ 1.0): 4px

### 4.2 节点视觉规范

**Owner节点**:
- 形状: 圆形
- 半径: `20 + Math.sqrt(personCount) * 3` (范围: 20px ~ 60px)
- 边框: 3px 白色描边
- 标签: 节点下方，白色文字，14px
- 徽章: 右上角显示人物数量

**人物节点**:
- 形状: 圆形
- 半径: Primary 30px, Secondary 20px
- 内容: 显示名字首字符
- 边框: 2px 白色描边
- 标签: 节点下方，白色文字，12px
- Hover: 放大1.2倍 + 光晕效果

**事件节点**:
- 形状: 菱形 (旋转45度的正方形)
- 尺寸: `15 + importance * 15` (范围: 15px ~ 30px)
- 颜色: 根据eventType映射
- 标签: 事件摘要(截断至15字)

### 4.3 连线视觉规范

**默认状态**:
- 直线连接两个节点
- 中点显示事件数量徽章(白色圆形背景，直径20px)

**Hover状态**:
- 线条高亮(亮度+30%)
- 徽章放大1.3倍
- 显示tooltip: "5个事件 | 朋友 | 情感: 积极"

**展开状态**:
- 原连线变为虚线
- 事件节点插入，均匀分布
- 分段连线: 人物A → 事件1 → 事件2 → ... → 人物B

---

## 5. 交互流程设计

### 5.1 第一层: Owner列表视图

**初始布局**:
- 所有Owner节点按创建时间排序
- 最新的Owner放置在画布中心
- 其他Owner使用D3力导向自动排布
- 节点之间保持最小间距，避免重叠

**交互**:
- **点击Owner**: 瞬间切换到该Owner的关系网络
- **Hover**: 节点放大 + tooltip (名称 + 统计)
- **搜索**: 匹配的Owner节点高亮闪烁
- **缩放/平移**: 滚轮缩放，拖拽平移

### 5.2 第二层: 人物关系网络视图

**布局算法**:
- Owner的primary人物放置在中心
- 使用D3 `forceSimulation`:
  - `forceLink`: 有关系的节点相互吸引
  - `forceManyBody`: 节点相互排斥
  - `forceCenter`: 整体向中心聚拢
  - `forceCollide`: 碰撞检测
- 运行300次迭代后稳定，瞬间渲染(无动画)

**交互**:
- **点击人物节点**: 右侧滑出信息面板
- **拖拽节点**: 手动调整位置，力模拟继续运行
- **点击连线**: 展开事件节点
- **双击空白 / ESC**: 返回Owner列表
- **Hover连线**: 显示关系信息tooltip

### 5.3 第三层: 事件展开

**展开逻辑**:
- 点击连线后加载该关系的所有事件
- 事件节点插入在两个人物之间，均匀分布
- 原连线消失，替换为分段连线
- 瞬间渲染，无动画

**交互**:
- **点击事件节点**: 侧边面板显示事件详情
- **Hover**: 显示事件摘要tooltip
- **再次点击连线区域**: 收起事件节点
- **支持多条连线同时展开**

### 5.4 导航与返回

**返回机制**:
- 双击空白区域
- 按ESC键
- 顶部面包屑: "所有Owner" 或 "所有Owner > 张三"

**状态保持**:
- 返回Owner列表时，恢复之前的缩放和平移
- 再次进入同一Owner时，重新计算布局

---

## 6. 代码架构设计

### 6.1 核心模块

**GraphEngine** (图谱引擎):
- 职责: 数据管理、渲染控制、状态切换
- 方法:
  - `loadOwners()`, `loadPersonGraph()`, `loadEvents()`
  - `render()`, `renderOwnerList()`, `renderPersonGraph()`
  - `switchToOwnerList()`, `switchToPersonGraph()`, `expandEvents()`

**LayoutEngine** (布局计算):
- 职责: 节点位置计算
- 方法:
  - `computeOwnerLayout(owners)` → 螺旋或力导向
  - `computePersonLayout(persons, relationships)` → D3 forceSimulation
  - `computeEventLayout(edge, events)` → 均匀分布

**InteractionManager** (交互管理):
- 职责: 事件监听、碰撞检测、交互反馈
- 方法:
  - `bindEvents()` → 绑定鼠标/键盘事件
  - `onClick()`, `onMouseMove()`, `onWheel()`, `onKeyDown()`

**DataManager** (数据管理):
- 职责: API请求、数据缓存、数据转换
- 方法:
  - `fetchOwners()`, `fetchPersonGraph()`, `fetchRelationshipEvents()`
  - `transformOwnerData()`, `transformPersonData()`, `transformEventData()`

**UIManager** (UI组件):
- 职责: Tooltip、侧边面板、加载状态、Toast提示
- 方法:
  - `showTooltip()`, `showSidePanel()`, `showLoading()`, `showToast()`

### 6.2 主应用入口

```javascript
class GraphApp {
  constructor() {
    this.engine = new GraphEngine(canvas);
    this.dataManager = new DataManager('/dashboard/api');
    this.interactionManager = new InteractionManager(this.engine);
    this.uiManager = new UIManager();
  }
  
  async init() {
    await this.loadInitialData();
    this.interactionManager.bindEvents();
    this.startRenderLoop();
  }
  
  startRenderLoop() {
    requestAnimationFrame(() => {
      if (this.engine.needsRedraw) {
        this.engine.render();
      }
    });
  }
}
```

### 6.3 文件结构

**单文件方案** (推荐):
```
api/static/graph.html  (所有代码内联，便于部署)
```

**多文件方案** (可选):
```
api/static/
├── graph.html
└── js/
    ├── graph-engine.js
    ├── interaction.js
    ├── data-manager.js
    ├── ui-manager.js
    └── graph-app.js
```

---

## 7. 错误处理与边界情况

### 7.1 数据异常

**空数据**:
- 无Owner: 显示 "暂无数据，请先创建对话"
- Owner无关系: 显示 "该用户暂无关系网络"
- 关系无事件: Toast提示 "该关系暂无事件记录"

**加载失败**:
- API请求失败时显示错误toast
- 保持当前视图，提供"重试"按钮

**数据不一致**:
- 关系指向的人物不存在 → 跳过该关系
- 事件参与者不在图谱中 → 标记为"外部参与者"

### 7.2 边界情况

**大数据量**:
- 单个Owner超过100人物 → 警告并限制渲染前100个
- 两人之间超过50事件 → 只显示最重要的前20个

**极端关系**:
- 某人物超过20个关系 → 调整力导向参数，增大画布范围
- 孤立节点(无关系) → 仍显示，位置在边缘

**窗口尺寸**:
- 响应式设计，监听 `window.resize`
- 移动端访问 → 提示 "建议使用桌面浏览器"

### 7.3 用户体验细节

**首次访问引导**:
- 半透明遮罩 + 操作提示
- 用户点击后消失，localStorage记录

**加载状态**:
- 数据加载时显示旋转动画
- 超过2秒显示 "正在加载关系数据..."

**操作反馈**:
- 所有点击有视觉反馈(闪烁/缩放)
- Hover时光标变为pointer
- 拖拽时光标变为grab/grabbing

---

## 8. 性能优化策略

### 8.1 渲染优化

**双Canvas架构**:
- 底层Canvas: 绘制静态图形(节点、边)
- 顶层Canvas: 绘制交互元素(hover高亮、选中状态)
- 仅在数据变化或交互时重绘

**渲染控制**:
- 使用 `requestAnimationFrame` 控制绘制频率
- 设置 `needsRedraw` 标志，避免无效重绘

### 8.2 碰撞检测优化

- 使用D3的 `quadtree` 四叉树加速点击检测
- 鼠标移动时，仅检测附近的节点和边

### 8.3 数据加载策略

- Owner列表: 一次性加载
- 人物关系网络: 按需加载(点击Owner时)
- 事件列表: 按需加载(点击连线时)
- 使用Map缓存已加载的数据

---

## 9. 测试与验证

### 9.1 功能测试

**核心流程**:
- Owner列表加载和显示
- 点击Owner展开关系网络
- 点击连线展开事件
- 双击/ESC返回
- 缩放、平移、拖拽节点

**边界情况**:
- 空数据、单节点、大量节点(100+)
- 网络请求失败
- 窗口resize

### 9.2 性能测试

- 使用Chrome DevTools Performance面板
- 监控渲染帧率(目标: 60fps)
- 测试大数据量(100节点 + 200边)

### 9.3 浏览器兼容性

- Chrome/Edge (主要支持)
- Firefox
- Safari (Canvas API兼容性检查)

---

## 10. 开发优先级

### Phase 1 (MVP - 最小可用版本)

- Owner列表视图
- 人物关系网络视图
- 基础交互(点击、缩放、返回)
- 连线显示事件数量
- 基础样式和配色

### Phase 2 (完整功能)

- 事件展开功能
- 侧边信息面板
- 搜索功能
- 完整的错误处理
- 性能优化

### Phase 3 (增强体验)

- 时间轴过滤
- 关系类型筛选
- 导出功能(PNG/JSON)
- 首次访问引导
- 主题切换

---

## 11. 后续扩展可能性

**功能增强**:
- 时间轴过滤: 只显示特定时间段的关系和事件
- 关系类型筛选: 按关系类型(朋友/家人/同事)过滤
- 事件类型筛选: 按事件类型(社交/工作/情感)过滤
- 导出功能: 导出为PNG图片或JSON数据
- 全屏模式: 隐藏工具栏，纯图谱展示
- 多Owner对比: 并排显示多个Owner的关系网络

**视觉优化**:
- 主题切换: 支持浅色主题
- 自定义配色: 用户自定义节点和连线颜色
- 动画选项: 提供"启用动画"选项
- 3D视图: 使用Three.js实现3D关系图谱(长期)

**智能功能**:
- 关系推荐: 基于事件内容推荐可能的关系
- 重要节点高亮: 自动识别核心人物
- 异常检测: 标记情感突变的关系

---

## 12. 总结

本设计文档定义了一个基于D3.js + Canvas的关系图谱可视化系统，核心特点：

1. **三层视图结构**: Owner列表 → 人物关系网络 → 事件展开
2. **以用户为中心**: 每个Owner是独立的关系孤岛
3. **渐进式信息展示**: 默认简洁，通过交互逐层展开
4. **高性能渲染**: Canvas + 力导向布局 + 优化策略
5. **现代化视觉**: 深色主题 + 颜色/粗细双重编码

该设计方案在美学、性能和可扩展性之间取得了良好的平衡，适合当前Memory System的数据规模和用户需求。
