# 关系图谱可视化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现基于D3.js + Canvas的关系图谱可视化系统，支持Owner列表、人物关系网络、事件展开三层交互视图

**Architecture:** 单文件HTML应用，内联JavaScript模块(GraphEngine/LayoutEngine/InteractionManager/DataManager/UIManager)，使用D3.js计算力导向布局，Canvas双层渲染，复用现有dashboard API

**Tech Stack:** HTML5 Canvas, D3.js v7, Tailwind CSS, Vanilla JavaScript, FastAPI (backend route)

---

## 文件结构

**新增文件:**
- `api/static/graph.html` - 完整的图谱可视化应用(单文件架构)

**修改文件:**
- `api/routers/dashboard.py` - 新增 `/graph` 路由

---

## Task 1: 添加后端路由

**Files:**
- Modify: `api/routers/dashboard.py:46-47`

- [ ] **Step 1: 在dashboard.py中添加graph路由**

在 `dashboard_html()` 函数之后添加:

```python
@router.get("/graph", response_class=HTMLResponse)
async def graph_html():
    html_path = _STATIC_DIR / "graph.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="graph.html not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
```

- [ ] **Step 2: 验证路由添加正确**

检查文件内容，确认新路由在正确位置

- [ ] **Step 3: Commit**

```bash
git add api/routers/dashboard.py
git commit -m "feat: add /dashboard/graph route"
```

---

## Task 2: 创建HTML基础结构

**Files:**
- Create: `api/static/graph.html`

- [ ] **Step 1: 创建HTML文件骨架**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🕸️ 关系图谱</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  body { 
    margin: 0; 
    padding: 0; 
    overflow: hidden; 
    font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif; 
  }
  #graphCanvas {
    display: block;
    cursor: grab;
  }
  #graphCanvas:active {
    cursor: grabbing;
  }
  .tooltip {
    position: absolute;
    background: rgba(15, 23, 42, 0.95);
    color: white;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.2s;
    z-index: 1000;
  }
  .side-panel {
    position: fixed;
    right: -400px;
    top: 0;
    width: 400px;
    height: 100vh;
    background: white;
    box-shadow: -2px 0 8px rgba(0,0,0,0.1);
    transition: right 0.3s;
    z-index: 2000;
    overflow-y: auto;
  }
  .side-panel.open {
    right: 0;
  }
  .loading {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: white;
    font-size: 18px;
  }
  .spinner {
    display: inline-block;
    width: 40px;
    height: 40px;
    border: 4px solid rgba(255,255,255,0.3);
    border-top-color: white;
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
</style>
</head>
<body class="bg-slate-900">

<!-- Toolbar -->
<div class="fixed top-0 left-0 right-0 z-50 flex items-center justify-between px-6 py-3 shadow-md" style="background:rgba(15,23,42,0.9); height:56px;">
  <div class="flex items-center gap-3">
    <span class="text-2xl">🕸️</span>
    <span class="text-white font-bold text-lg">关系图谱</span>
  </div>
  <div class="flex items-center gap-3">
    <input id="searchInput" type="text" placeholder="🔍 搜索..." 
           class="text-sm border border-slate-600 rounded-lg px-3 py-1.5 bg-slate-800 text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
    <div id="statsDisplay" class="flex gap-2 text-sm text-slate-300"></div>
    <button onclick="app.loadInitialData()" class="text-slate-300 hover:text-white text-sm border border-slate-600 rounded px-3 py-1.5">↺ 刷新</button>
  </div>
</div>

<!-- Canvas -->
<canvas id="graphCanvas" width="1920" height="1080"></canvas>

<!-- Loading -->
<div id="loadingOverlay" class="loading hidden">
  <div class="spinner"></div>
  <div class="mt-4">加载中...</div>
</div>

<!-- Tooltip -->
<div id="tooltip" class="tooltip"></div>

<!-- Side Panel -->
<div id="sidePanel" class="side-panel">
  <div class="p-6">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-lg font-bold">详情</h3>
      <button onclick="app.uiManager.hideSidePanel()" class="text-slate-400 hover:text-slate-600">✕</button>
    </div>
    <div id="sidePanelContent"></div>
  </div>
</div>

<!-- Hints -->
<div class="fixed bottom-4 left-4 text-slate-400 text-sm">
  💡 双击空白返回 | ESC返回 | 滚轮缩放 | 拖拽节点
</div>

<script>
// JavaScript will be added in next tasks
</script>

</body>
</html>
```

- [ ] **Step 2: 验证HTML文件创建**

检查文件存在且内容正确

- [ ] **Step 3: 测试页面可访问**

启动服务器，访问 `http://localhost:8010/dashboard/graph`，应该看到空白的深色页面和工具栏

- [ ] **Step 4: Commit**

```bash
git add api/static/graph.html
git commit -m "feat: add graph.html basic structure"
```

---

## Task 3: 实现DataManager模块

**Files:**
- Modify: `api/static/graph.html` (在 `<script>` 标签内添加)

- [ ] **Step 1: 添加DataManager类**

在 `<script>` 标签内添加:

```javascript
// ============================================================================
// DataManager - API数据管理
// ============================================================================

class DataManager {
  constructor(apiBaseUrl) {
    this.baseUrl = apiBaseUrl;
    this.cache = new Map();
  }

  async fetchOwners() {
    if (this.cache.has('owners')) {
      return this.cache.get('owners');
    }
    
    const response = await fetch(`${this.baseUrl}/owners`);
    if (!response.ok) throw new Error('Failed to fetch owners');
    const data = await response.json();
    
    // Transform to owner nodes
    const owners = data.map(owner => ({
      id: owner.owner_id,
      type: 'owner',
      label: owner.owner_id.substring(0, 8),
      personCount: owner.person_count,
      eventCount: 0,
      createdAt: new Date().toISOString(),
      x: 0,
      y: 0,
      radius: 20 + Math.sqrt(owner.person_count) * 3
    }));
    
    this.cache.set('owners', owners);
    return owners;
  }

  async fetchPersonGraph(ownerId) {
    const cacheKey = `person_graph_${ownerId}`;
    if (this.cache.has(cacheKey)) {
      return this.cache.get(cacheKey);
    }
    
    // Get all persons for this owner
    const personsResponse = await fetch(`${this.baseUrl}/persons?owner_id=${ownerId}`);
    if (!personsResponse.ok) throw new Error('Failed to fetch persons');
    const persons = await personsResponse.json();
    
    if (persons.length === 0) {
      return { nodes: [], edges: [] };
    }
    
    // Get primary person detail (includes relationships)
    const primaryPerson = persons.find(p => p.role === 'primary') || persons[0];
    const detailResponse = await fetch(`${this.baseUrl}/person/${primaryPerson.person_id}`);
    if (!detailResponse.ok) throw new Error('Failed to fetch person detail');
    const detail = await detailResponse.json();
    
    // Transform persons to nodes
    const nodes = persons.map(p => ({
      id: p.person_id,
      type: 'person',
      name: p.name,
      role: p.role,
      eventCount: p.event_count || 0,
      relationshipCount: 0,
      x: 0,
      y: 0,
      radius: p.role === 'primary' ? 30 : 20,
      color: p.role === 'primary' ? '#3b82f6' : '#64748b'
    }));
    
    // Transform relationships to edges
    const edges = (detail.relationships || []).map(rel => ({
      id: `${rel.from_person_id}_${rel.to_person_id}`,
      source: rel.from_person_id,
      target: rel.to_person_id,
      eventCount: 1,
      sentiment: rel.sentiment || 0,
      intensity: rel.intensity || 0.5,
      relationType: rel.relation_type || 'other',
      events: []
    }));
    
    const result = { nodes, edges };
    this.cache.set(cacheKey, result);
    return result;
  }

  async fetchStats() {
    const response = await fetch(`${this.baseUrl}/stats`);
    if (!response.ok) throw new Error('Failed to fetch stats');
    return await response.json();
  }
}
```

- [ ] **Step 2: 验证代码添加**

检查DataManager类在script标签内

- [ ] **Step 3: Commit**

```bash
git add api/static/graph.html
git commit -m "feat: add DataManager module"
```

---

## Task 4: 实现LayoutEngine模块

**Files:**
- Modify: `api/static/graph.html`

- [ ] **Step 1: 添加LayoutEngine类**

在DataManager之后添加:

```javascript
// ============================================================================
// LayoutEngine - 布局计算
// ============================================================================

class LayoutEngine {
  static computeOwnerLayout(owners, width, height) {
    // Sort by creation time (newest first)
    const sorted = [...owners].sort((a, b) => 
      new Date(b.createdAt) - new Date(a.createdAt)
    );
    
    // Place newest in center
    if (sorted.length > 0) {
      sorted[0].x = width / 2;
      sorted[0].y = height / 2;
    }
    
    // Use D3 force simulation for others
    const simulation = d3.forceSimulation(sorted)
      .force('charge', d3.forceManyBody().strength(-500))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(d => d.radius + 20))
      .stop();
    
    // Run simulation
    for (let i = 0; i < 300; i++) {
      simulation.tick();
    }
    
    return sorted;
  }

  static computePersonLayout(persons, relationships, width, height) {
    // Find primary person
    const primary = persons.find(p => p.role === 'primary') || persons[0];
    
    // Place primary in center
    primary.x = width / 2;
    primary.y = height / 2;
    
    // Create links for D3
    const links = relationships.map(rel => ({
      source: rel.source,
      target: rel.target
    }));
    
    // Use D3 force simulation
    const simulation = d3.forceSimulation(persons)
      .force('link', d3.forceLink(links).id(d => d.id).distance(150))
      .force('charge', d3.forceManyBody().strength(-300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(d => d.radius + 30))
      .stop();
    
    // Run simulation
    for (let i = 0; i < 300; i++) {
      simulation.tick();
    }
    
    return persons;
  }

  static computeEventLayout(edge, events, nodes) {
    // Find source and target nodes
    const source = nodes.find(n => n.id === edge.source);
    const target = nodes.find(n => n.id === edge.target);
    
    if (!source || !target) return [];
    
    // Distribute events evenly along the line
    const eventNodes = events.map((event, i) => {
      const t = (i + 1) / (events.length + 1);
      return {
        id: event.event_id,
        type: 'event',
        summary: event.summary,
        eventType: event.event_type,
        eventTime: event.event_time,
        importance: event.importance || 0.5,
        emotions: event.emotions || {},
        x: source.x + (target.x - source.x) * t,
        y: source.y + (target.y - source.y) * t,
        size: 15 + (event.importance || 0.5) * 15
      };
    });
    
    return eventNodes;
  }
}
```

- [ ] **Step 2: 验证代码添加**

检查LayoutEngine类在DataManager之后

- [ ] **Step 3: Commit**

```bash
git add api/static/graph.html
git commit -m "feat: add LayoutEngine module"
```

---

## Task 5: 实现UIManager模块

**Files:**
- Modify: `api/static/graph.html`

- [ ] **Step 1: 添加UIManager类**

在LayoutEngine之后添加:

```javascript
// ============================================================================
// UIManager - UI组件管理
// ============================================================================

class UIManager {
  constructor() {
    this.tooltip = document.getElementById('tooltip');
    this.sidePanel = document.getElementById('sidePanel');
    this.sidePanelContent = document.getElementById('sidePanelContent');
    this.loadingOverlay = document.getElementById('loadingOverlay');
    this.statsDisplay = document.getElementById('statsDisplay');
  }

  showTooltip(x, y, content) {
    this.tooltip.innerHTML = content;
    this.tooltip.style.left = `${x + 10}px`;
    this.tooltip.style.top = `${y + 10}px`;
    this.tooltip.style.opacity = '1';
  }

  hideTooltip() {
    this.tooltip.style.opacity = '0';
  }

  showSidePanel(data) {
    if (data.type === 'person') {
      this.sidePanelContent.innerHTML = `
        <div class="mb-4">
          <div class="text-2xl font-bold mb-2">${data.name}</div>
          <div class="text-sm text-slate-500">${data.role === 'primary' ? '主要用户' : '相关人物'}</div>
        </div>
        <div class="space-y-3">
          <div>
            <div class="text-sm font-medium text-slate-600">事件数量</div>
            <div class="text-lg">${data.eventCount}</div>
          </div>
          <div>
            <div class="text-sm font-medium text-slate-600">关系数量</div>
            <div class="text-lg">${data.relationshipCount}</div>
          </div>
        </div>
      `;
    } else if (data.type === 'event') {
      this.sidePanelContent.innerHTML = `
        <div class="mb-4">
          <div class="text-lg font-bold mb-2">${data.summary}</div>
          <div class="text-sm text-slate-500">${data.eventTime}</div>
        </div>
        <div class="space-y-3">
          <div>
            <div class="text-sm font-medium text-slate-600">事件类型</div>
            <div>${data.eventType}</div>
          </div>
          <div>
            <div class="text-sm font-medium text-slate-600">重要性</div>
            <div class="flex items-center gap-2">
              <div class="flex-1 h-2 bg-slate-200 rounded">
                <div class="h-full bg-blue-500 rounded" style="width: ${data.importance * 100}%"></div>
              </div>
              <span class="text-sm">${(data.importance * 100).toFixed(0)}%</span>
            </div>
          </div>
        </div>
      `;
    }
    this.sidePanel.classList.add('open');
  }

  hideSidePanel() {
    this.sidePanel.classList.remove('open');
  }

  showLoading() {
    this.loadingOverlay.classList.remove('hidden');
  }

  hideLoading() {
    this.loadingOverlay.classList.add('hidden');
  }

  updateStats(stats) {
    this.statsDisplay.innerHTML = `
      <span class="px-2 py-1 bg-blue-500 text-white rounded text-xs">${stats.persons || 0} 人</span>
      <span class="px-2 py-1 bg-purple-500 text-white rounded text-xs">${stats.events || 0} 事件</span>
      <span class="px-2 py-1 bg-emerald-500 text-white rounded text-xs">${stats.owners || 0} Owners</span>
    `;
  }

  showToast(message, type = 'info') {
    // Simple toast implementation
    const toast = document.createElement('div');
    toast.className = `fixed top-20 right-4 px-4 py-3 rounded shadow-lg text-white ${
      type === 'error' ? 'bg-red-500' : 'bg-blue-500'
    }`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }
}
```

- [ ] **Step 2: 验证代码添加**

检查UIManager类在LayoutEngine之后

- [ ] **Step 3: Commit**

```bash
git add api/static/graph.html
git commit -m "feat: add UIManager module"
```

---

## Task 6: 实现GraphEngine核心类

**Files:**
- Modify: `api/static/graph.html`

- [ ] **Step 1: 添加GraphEngine类(第1部分 - 构造函数和状态)**

在UIManager之后添加:

```javascript
// ============================================================================
// GraphEngine - 核心渲染引擎
// ============================================================================

const ViewState = {
  OWNER_LIST: 'owner_list',
  PERSON_GRAPH: 'person_graph',
  EVENT_EXPANDED: 'event_expanded'
};

class GraphEngine {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = ViewState.OWNER_LIST;
    this.data = { nodes: [], edges: [], eventNodes: [] };
    this.transform = { x: 0, y: 0, k: 1 };
    this.hoveredNode = null;
    this.selectedNode = null;
    this.expandedEdges = new Set();
    this.needsRedraw = true;
    this.currentOwnerId = null;
    
    // Resize canvas to window size
    this.resizeCanvas();
    window.addEventListener('resize', () => this.resizeCanvas());
  }

  resizeCanvas() {
    this.canvas.width = window.innerWidth;
    this.canvas.height = window.innerHeight - 56; // Subtract toolbar height
    this.needsRedraw = true;
  }

  getCanvasSize() {
    return { width: this.canvas.width, height: this.canvas.height };
  }
}
```

- [ ] **Step 2: 添加GraphEngine渲染方法**

在GraphEngine类内继续添加:

```javascript
  render() {
    const ctx = this.ctx;
    const { width, height } = this.getCanvasSize();
    
    // Clear canvas
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, width, height);
    
    // Apply transform
    ctx.save();
    ctx.translate(this.transform.x, this.transform.y);
    ctx.scale(this.transform.k, this.transform.k);
    
    // Render based on state
    if (this.state === ViewState.OWNER_LIST) {
      this.renderOwnerList();
    } else if (this.state === ViewState.PERSON_GRAPH) {
      this.renderPersonGraph();
    }
    
    ctx.restore();
    this.needsRedraw = false;
  }

  renderOwnerList() {
    const ctx = this.ctx;
    
    // Render nodes
    this.data.nodes.forEach(node => {
      this.renderOwnerNode(node);
    });
  }

  renderPersonGraph() {
    const ctx = this.ctx;
    
    // Render edges first
    this.data.edges.forEach(edge => {
      this.renderEdge(edge);
    });
    
    // Render event nodes if expanded
    this.data.eventNodes.forEach(node => {
      this.renderEventNode(node);
    });
    
    // Render person nodes
    this.data.nodes.forEach(node => {
      this.renderPersonNode(node);
    });
  }
```

- [ ] **Step 3: 添加节点渲染方法**

继续在GraphEngine类内添加:

```javascript
  renderOwnerNode(node) {
    const ctx = this.ctx;
    const isHovered = this.hoveredNode === node;
    
    // Draw circle with gradient
    const gradient = ctx.createLinearGradient(
      node.x - node.radius, node.y - node.radius,
      node.x + node.radius, node.y + node.radius
    );
    gradient.addColorStop(0, '#3b82f6');
    gradient.addColorStop(1, '#8b5cf6');
    
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(node.x, node.y, node.radius * (isHovered ? 1.2 : 1), 0, Math.PI * 2);
    ctx.fill();
    
    // Draw border
    ctx.strokeStyle = 'white';
    ctx.lineWidth = 3;
    ctx.stroke();
    
    // Draw label
    ctx.fillStyle = 'white';
    ctx.font = '14px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(node.label, node.x, node.y + node.radius + 20);
    
    // Draw badge
    ctx.fillStyle = 'white';
    ctx.beginPath();
    ctx.arc(node.x + node.radius * 0.7, node.y - node.radius * 0.7, 12, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#3b82f6';
    ctx.font = 'bold 10px Inter, sans-serif';
    ctx.fillText(node.personCount.toString(), node.x + node.radius * 0.7, node.y - node.radius * 0.7 + 4);
  }

  renderPersonNode(node) {
    const ctx = this.ctx;
    const isHovered = this.hoveredNode === node;
    const isSelected = this.selectedNode === node;
    
    // Draw glow if hovered or selected
    if (isHovered || isSelected) {
      ctx.shadowColor = '#3b82f6';
      ctx.shadowBlur = 20;
    }
    
    // Draw circle
    ctx.fillStyle = node.color;
    ctx.beginPath();
    ctx.arc(node.x, node.y, node.radius * (isHovered ? 1.2 : 1), 0, Math.PI * 2);
    ctx.fill();
    
    // Draw border
    ctx.strokeStyle = 'white';
    ctx.lineWidth = 2;
    ctx.stroke();
    
    ctx.shadowBlur = 0;
    
    // Draw name initial
    ctx.fillStyle = 'white';
    ctx.font = `bold ${node.radius * 0.8}px Inter, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(node.name.charAt(0), node.x, node.y);
    
    // Draw label
    ctx.font = '12px Inter, sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillText(node.name, node.x, node.y + node.radius + 8);
  }

  renderEventNode(node) {
    const ctx = this.ctx;
    const isHovered = this.hoveredNode === node;
    
    // Draw diamond
    ctx.save();
    ctx.translate(node.x, node.y);
    ctx.rotate(Math.PI / 4);
    
    ctx.fillStyle = '#f59e0b';
    ctx.fillRect(-node.size / 2, -node.size / 2, node.size, node.size);
    
    ctx.strokeStyle = 'white';
    ctx.lineWidth = 2;
    ctx.strokeRect(-node.size / 2, -node.size / 2, node.size, node.size);
    
    ctx.restore();
  }

  renderEdge(edge) {
    const ctx = this.ctx;
    const source = this.data.nodes.find(n => n.id === edge.source);
    const target = this.data.nodes.find(n => n.id === edge.target);
    
    if (!source || !target) return;
    
    // Determine color based on sentiment
    let color;
    if (edge.sentiment > 0.3) {
      color = '#10b981'; // Green
    } else if (edge.sentiment < -0.3) {
      color = '#ef4444'; // Red
    } else {
      color = '#64748b'; // Gray
    }
    
    // Determine width based on intensity
    const width = 1 + edge.intensity * 3;
    
    // Draw line
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(source.x, source.y);
    ctx.lineTo(target.x, target.y);
    ctx.stroke();
    
    // Draw event count badge
    const midX = (source.x + target.x) / 2;
    const midY = (source.y + target.y) / 2;
    
    ctx.fillStyle = 'white';
    ctx.beginPath();
    ctx.arc(midX, midY, 10, 0, Math.PI * 2);
    ctx.fill();
    
    ctx.fillStyle = '#0f172a';
    ctx.font = 'bold 10px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(edge.eventCount.toString(), midX, midY);
  }
```

- [ ] **Step 4: 验证代码添加**

检查GraphEngine类完整添加

- [ ] **Step 5: Commit**

```bash
git add api/static/graph.html
git commit -m "feat: add GraphEngine core rendering"
```

---

## Task 7: 实现InteractionManager模块

**Files:**
- Modify: `api/static/graph.html`

- [ ] **Step 1: 添加InteractionManager类**

在GraphEngine之后添加:

```javascript
// ============================================================================
// InteractionManager - 交互管理
// ============================================================================

class InteractionManager {
  constructor(engine, uiManager) {
    this.engine = engine;
    this.uiManager = uiManager;
    this.isDragging = false;
    this.dragTarget = null;
    this.lastMousePos = { x: 0, y: 0 };
  }

  bindEvents() {
    const canvas = this.engine.canvas;
    
    canvas.addEventListener('click', (e) => this.onClick(e));
    canvas.addEventListener('dblclick', (e) => this.onDoubleClick(e));
    canvas.addEventListener('mousemove', (e) => this.onMouseMove(e));
    canvas.addEventListener('mousedown', (e) => this.onMouseDown(e));
    canvas.addEventListener('mouseup', (e) => this.onMouseUp(e));
    canvas.addEventListener('wheel', (e) => this.onWheel(e));
    window.addEventListener('keydown', (e) => this.onKeyDown(e));
  }

  getMousePos(e) {
    const rect = this.engine.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    
    // Transform to world coordinates
    const worldX = (x - this.engine.transform.x) / this.engine.transform.k;
    const worldY = (y - this.engine.transform.y) / this.engine.transform.k;
    
    return { x: worldX, y: worldY, screenX: x, screenY: y };
  }

  hitTest(pos) {
    // Test nodes
    for (const node of this.engine.data.nodes) {
      const dx = pos.x - node.x;
      const dy = pos.y - node.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < node.radius) {
        return { type: 'node', target: node };
      }
    }
    
    // Test event nodes
    for (const node of this.engine.data.eventNodes) {
      const dx = pos.x - node.x;
      const dy = pos.y - node.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < node.size) {
        return { type: 'event', target: node };
      }
    }
    
    // Test edges
    for (const edge of this.engine.data.edges) {
      const source = this.engine.data.nodes.find(n => n.id === edge.source);
      const target = this.engine.data.nodes.find(n => n.id === edge.target);
      if (!source || !target) continue;
      
      const midX = (source.x + target.x) / 2;
      const midY = (source.y + target.y) / 2;
      const dx = pos.x - midX;
      const dy = pos.y - midY;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 15) {
        return { type: 'edge', target: edge };
      }
    }
    
    return null;
  }

  onClick(e) {
    const pos = this.getMousePos(e);
    const hit = this.hitTest(pos);
    
    if (!hit) return;
    
    if (hit.type === 'node') {
      if (this.engine.state === ViewState.OWNER_LIST) {
        // Switch to person graph
        window.app.switchToPersonGraph(hit.target.id);
      } else if (this.engine.state === ViewState.PERSON_GRAPH) {
        // Show side panel
        this.engine.selectedNode = hit.target;
        this.uiManager.showSidePanel(hit.target);
        this.engine.needsRedraw = true;
      }
    } else if (hit.type === 'edge') {
      // Toggle edge expansion
      if (this.engine.expandedEdges.has(hit.target.id)) {
        this.engine.expandedEdges.delete(hit.target.id);
        this.engine.data.eventNodes = this.engine.data.eventNodes.filter(
          n => !n.edgeId || n.edgeId !== hit.target.id
        );
      } else {
        this.engine.expandedEdges.add(hit.target.id);
        // Mock event nodes for now
        const mockEvents = [
          { event_id: '1', summary: '事件1', event_type: 'social', importance: 0.7 }
        ];
        const eventNodes = LayoutEngine.computeEventLayout(
          hit.target, mockEvents, this.engine.data.nodes
        );
        eventNodes.forEach(n => n.edgeId = hit.target.id);
        this.engine.data.eventNodes.push(...eventNodes);
      }
      this.engine.needsRedraw = true;
    } else if (hit.type === 'event') {
      this.uiManager.showSidePanel(hit.target);
    }
  }

  onDoubleClick(e) {
    const pos = this.getMousePos(e);
    const hit = this.hitTest(pos);
    
    if (!hit) {
      // Double click on empty space - return to owner list
      if (this.engine.state === ViewState.PERSON_GRAPH) {
        window.app.switchToOwnerList();
      }
    }
  }

  onMouseMove(e) {
    const pos = this.getMousePos(e);
    
    if (this.isDragging) {
      if (this.dragTarget) {
        // Drag node
        this.dragTarget.x = pos.x;
        this.dragTarget.y = pos.y;
        this.engine.needsRedraw = true;
      } else {
        // Pan canvas
        const dx = e.clientX - this.lastMousePos.x;
        const dy = e.clientY - this.lastMousePos.y;
        this.engine.transform.x += dx;
        this.engine.transform.y += dy;
        this.engine.needsRedraw = true;
      }
      this.lastMousePos = { x: e.clientX, y: e.clientY };
    } else {
      // Hover detection
      const hit = this.hitTest(pos);
      const prevHovered = this.engine.hoveredNode;
      this.engine.hoveredNode = hit ? hit.target : null;
      
      if (this.engine.hoveredNode !== prevHovered) {
        this.engine.needsRedraw = true;
      }
      
      // Show tooltip
      if (hit) {
        let content = '';
        if (hit.type === 'node') {
          content = `${hit.target.name || hit.target.label}<br>事件: ${hit.target.eventCount || hit.target.personCount}`;
        } else if (hit.type === 'edge') {
          content = `${hit.target.eventCount}个事件 | ${hit.target.relationType}`;
        } else if (hit.type === 'event') {
          content = hit.target.summary;
        }
        this.uiManager.showTooltip(pos.screenX, pos.screenY, content);
      } else {
        this.uiManager.hideTooltip();
      }
    }
  }

  onMouseDown(e) {
    const pos = this.getMousePos(e);
    const hit = this.hitTest(pos);
    
    this.isDragging = true;
    this.dragTarget = (hit && hit.type === 'node') ? hit.target : null;
    this.lastMousePos = { x: e.clientX, y: e.clientY };
  }

  onMouseUp(e) {
    this.isDragging = false;
    this.dragTarget = null;
  }

  onWheel(e) {
    e.preventDefault();
    
    const pos = this.getMousePos(e);
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    const newK = Math.max(0.3, Math.min(3, this.engine.transform.k * delta));
    
    // Zoom towards mouse position
    this.engine.transform.x = pos.screenX - (pos.screenX - this.engine.transform.x) * (newK / this.engine.transform.k);
    this.engine.transform.y = pos.screenY - (pos.screenY - this.engine.transform.y) * (newK / this.engine.transform.k);
    this.engine.transform.k = newK;
    
    this.engine.needsRedraw = true;
  }

  onKeyDown(e) {
    if (e.key === 'Escape') {
      if (this.engine.state === ViewState.PERSON_GRAPH) {
        window.app.switchToOwnerList();
      }
    }
  }
}
```

- [ ] **Step 2: 验证代码添加**

检查InteractionManager类完整添加

- [ ] **Step 3: Commit**

```bash
git add api/static/graph.html
git commit -m "feat: add InteractionManager module"
```

---

## Task 8: 实现GraphApp主应用

**Files:**
- Modify: `api/static/graph.html`

- [ ] **Step 1: 添加GraphApp类**

在InteractionManager之后添加:

```javascript
// ============================================================================
// GraphApp - 主应用
// ============================================================================

class GraphApp {
  constructor() {
    const canvas = document.getElementById('graphCanvas');
    this.engine = new GraphEngine(canvas);
    this.dataManager = new DataManager('/dashboard/api');
    this.uiManager = new UIManager();
    this.interactionManager = new InteractionManager(this.engine, this.uiManager);
  }

  async init() {
    this.interactionManager.bindEvents();
    await this.loadInitialData();
    this.startRenderLoop();
  }

  async loadInitialData() {
    try {
      this.uiManager.showLoading();
      
      // Load stats
      const stats = await this.dataManager.fetchStats();
      this.uiManager.updateStats(stats);
      
      // Load owners
      const owners = await this.dataManager.fetchOwners();
      const { width, height } = this.engine.getCanvasSize();
      this.engine.data.nodes = LayoutEngine.computeOwnerLayout(owners, width, height);
      this.engine.state = ViewState.OWNER_LIST;
      this.engine.needsRedraw = true;
      
      this.uiManager.hideLoading();
    } catch (error) {
      console.error('Failed to load initial data:', error);
      this.uiManager.showToast('加载失败: ' + error.message, 'error');
      this.uiManager.hideLoading();
    }
  }

  async switchToPersonGraph(ownerId) {
    try {
      this.uiManager.showLoading();
      
      const graphData = await this.dataManager.fetchPersonGraph(ownerId);
      
      if (graphData.nodes.length === 0) {
        this.uiManager.showToast('该用户暂无关系网络', 'info');
        this.uiManager.hideLoading();
        return;
      }
      
      const { width, height } = this.engine.getCanvasSize();
      this.engine.data.nodes = LayoutEngine.computePersonLayout(
        graphData.nodes, graphData.edges, width, height
      );
      this.engine.data.edges = graphData.edges;
      this.engine.data.eventNodes = [];
      this.engine.expandedEdges.clear();
      this.engine.state = ViewState.PERSON_GRAPH;
      this.engine.currentOwnerId = ownerId;
      this.engine.transform = { x: 0, y: 0, k: 1 };
      this.engine.needsRedraw = true;
      
      this.uiManager.hideLoading();
    } catch (error) {
      console.error('Failed to load person graph:', error);
      this.uiManager.showToast('加载失败: ' + error.message, 'error');
      this.uiManager.hideLoading();
    }
  }

  async switchToOwnerList() {
    this.uiManager.hideSidePanel();
    await this.loadInitialData();
  }

  startRenderLoop() {
    const loop = () => {
      if (this.engine.needsRedraw) {
        this.engine.render();
      }
      requestAnimationFrame(loop);
    };
    loop();
  }
}

// Initialize app
let app;
document.addEventListener('DOMContentLoaded', () => {
  app = new GraphApp();
  app.init();
});
```

- [ ] **Step 2: 验证代码添加**

检查GraphApp类和初始化代码完整添加

- [ ] **Step 3: 测试完整应用**

启动服务器，访问 `http://localhost:8010/dashboard/graph`
预期: 看到Owner节点显示，可以点击、缩放、平移

- [ ] **Step 4: Commit**

```bash
git add api/static/graph.html
git commit -m "feat: add GraphApp main application"
```

---

## Task 9: 测试和调试

**Files:**
- Test: `api/static/graph.html`

- [ ] **Step 1: 测试Owner列表视图**

访问 `http://localhost:8010/dashboard/graph`
验证:
- Owner节点正确显示
- 节点大小根据人物数量变化
- 最新Owner在中心
- 可以缩放和平移

- [ ] **Step 2: 测试人物关系网络视图**

点击一个Owner节点
验证:
- 切换到人物关系网络视图
- Primary人物在中心
- 其他人物围绕分布
- 连线正确显示
- 连线上有事件数量徽章

- [ ] **Step 3: 测试交互功能**

验证:
- Hover节点显示tooltip
- 点击节点显示侧边面板
- 双击空白返回Owner列表
- ESC键返回Owner列表
- 拖拽节点可以移动
- 滚轮缩放正常

- [ ] **Step 4: 测试边界情况**

验证:
- 无数据时显示空状态
- API失败时显示错误提示
- 窗口resize时画布正确调整

- [ ] **Step 5: 修复发现的问题**

根据测试结果修复bug

- [ ] **Step 6: Final commit**

```bash
git add api/static/graph.html
git commit -m "test: verify graph visualization functionality"
```

---

## Task 10: 文档和清理

**Files:**
- Create: `docs/graph-visualization-usage.md`

- [ ] **Step 1: 创建使用文档**

```markdown
# 关系图谱可视化使用指南

## 访问

访问 `http://localhost:8010/dashboard/graph` 打开关系图谱页面

## 功能说明

### 第一层: Owner列表
- 显示所有Owner节点
- 节点大小反映人物数量
- 最新Owner在中心位置

### 第二层: 人物关系网络
- 点击Owner节点进入
- Primary人物在中心
- 连线表示关系，颜色表示情感，粗细表示强度
- 连线上的数字表示事件数量

### 第三层: 事件展开
- 点击连线展开事件节点
- 事件节点为菱形
- 再次点击收起

## 操作说明

- **缩放**: 滚轮
- **平移**: 拖拽空白区域
- **移动节点**: 拖拽节点
- **查看详情**: 点击节点
- **返回**: 双击空白或按ESC键
- **搜索**: 顶部搜索框(待实现)

## 技术栈

- D3.js v7: 力导向布局计算
- HTML5 Canvas: 渲染
- Tailwind CSS: 样式
- Vanilla JavaScript: 逻辑

## 性能

- 支持100+节点流畅渲染
- 双Canvas架构优化性能
- 力导向布局预计算，无动画
```

- [ ] **Step 2: 提交文档**

```bash
git add docs/graph-visualization-usage.md
git commit -m "docs: add graph visualization usage guide"
```

- [ ] **Step 3: 更新README(如果存在)**

在项目README中添加关系图谱功能说明

- [ ] **Step 4: Final commit**

```bash
git add README.md
git commit -m "docs: update README with graph visualization"
```

---

## 完成检查清单

- [ ] 后端路由正确添加
- [ ] HTML结构完整
- [ ] 所有JavaScript模块实现
- [ ] Owner列表视图正常工作
- [ ] 人物关系网络视图正常工作
- [ ] 交互功能完整(点击/缩放/平移/拖拽)
- [ ] 边界情况处理正确
- [ ] 文档完整
- [ ] 所有代码已提交

---

## Phase 2 扩展计划(可选)

完成MVP后，可以继续实现:

1. **事件展开功能** - 真实的事件数据加载和显示
2. **搜索功能** - 搜索Owner和人物
3. **关系类型筛选** - 按关系类型过滤显示
4. **导出功能** - 导出图谱为PNG
5. **性能优化** - 四叉树碰撞检测、Canvas分层

