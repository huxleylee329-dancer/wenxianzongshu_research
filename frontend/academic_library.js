/* Private library controls. Text from documents/providers is never HTML. */
(() => {
  'use strict';
  let loaded = false;
  let busy = false;
  let editing = false;
  let policyBusy = false;
  let policyAvailable = null;
  let policySession = null;
  let policyProposal = null;
  let policyRevision = 0;
  let policyScopeKey = '';
  const byId = (id) => document.getElementById(id);
  const labels = { openalex: 'OpenAlex', arxiv: 'arXiv', semantic_scholar: 'Semantic Scholar', pubmed_central: 'PubMed Central', hal: 'HAL', osti: 'OSTI · 能源材料', openaire: 'OpenAIRE', crossref: 'Crossref · DOI', europe_pmc: 'Europe PMC', zenodo: 'Zenodo' };
  const kinds = { metadata_only: '仅元数据', abstract: '摘要证据', full_text: '已解析全文', unavailable: '无法获取正文' };

  async function api(path, options = {}) {
    const response = await fetch(`/api/academic-library${path}`, { ...options, credentials: 'same-origin' });
    if (!response.ok) throw new Error(response.status === 403 ? '知识库仅允许本机同源访问；远程部署需单独配置访问凭证。' : `知识库操作失败（HTTP ${response.status}），未自动重试。`);
    return response.json();
  }
  function note(message) { byId('academicLibraryStatus').textContent = message; }
  function controls() {
    byId('academicLibraryControls').querySelectorAll('button, input, select, textarea').forEach((node) => { node.disabled = busy || editing || policyBusy; });
    policyControls();
  }
  async function action(callback) {
    if (busy || editing || policyBusy) return;
    editing = true; controls();
    try { await callback(); } catch (error) { note(error.message); }
    finally { editing = false; controls(); }
  }
  function selectedDocuments() {
    return [...byId('academicLibraryDocuments').querySelectorAll('input:checked')].map((node) => node.value);
  }
  function safeLink(container, label, url) {
    try {
      const parsed = new URL(url, window.location.origin);
      const local = parsed.origin === window.location.origin && /^\/api\/academic-library\/documents\/[0-9a-f]{32}\/original$/.test(parsed.pathname);
      if (!local && (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.origin === window.location.origin)) return;
      const anchor = document.createElement('a'); anchor.href = parsed.href; anchor.textContent = label;
      anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; container.append(anchor);
    } catch (_) { /* Invalid metadata is not a navigation target. */ }
  }
  async function refresh() {
    const selected = new Set(selectedDocuments());
    const data = await api('/documents');
    const list = byId('academicLibraryDocuments'); list.replaceChildren();
    (data.documents || []).forEach((doc) => {
      const row = document.createElement('div'); row.className = 'library-document';
      const label = document.createElement('label'); const box = document.createElement('input'); box.type = 'checkbox'; box.value = doc.id; box.checked = selected.has(doc.id);
      const title = document.createElement('span'); title.textContent = `${doc.filename} · ${doc.chunk_count}片段`; label.append(box, title);
      const preview = document.createElement('button'); preview.type = 'button'; preview.className = 'text-button'; preview.textContent = '查看';
      preview.addEventListener('click', () => action(async () => {
        const result = await api(`/documents/${encodeURIComponent(doc.id)}/passages`);
        renderPassages(result.passages || []);
      }));
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button'; remove.textContent = '删除';
      remove.addEventListener('click', () => {
        if (!window.confirm(`删除 ${doc.filename} 的原件和索引？既有报告中的本地原件链接将失效。`)) return;
        action(async () => { await api(`/documents/${encodeURIComponent(doc.id)}`, { method: 'DELETE' }); await refresh(); note('原件及活动索引已删除。'); });
      });
      row.append(label, preview, remove); list.append(row);
    });
    if (!data.documents.length) list.textContent = '尚未导入文档。导入后勾选文档可限定片段检索范围。';
    policyScopeChanged();
    controls();
  }
  function renderPassages(passages) {
    const panel = byId('academicLibraryResults'); panel.replaceChildren();
    passages.forEach((p) => {
      const article = document.createElement('article'); const heading = document.createElement('strong'); const body = document.createElement('p');
      heading.textContent = `${p.title || ''} ${p.locator || ''}`; body.textContent = p.text || '';
      article.append(heading, body); panel.append(article);
    });
    if (!passages.length) panel.textContent = '未检索到匹配片段，不会补造证据。';
  }
  async function load() {
    if (loaded) return;
    loaded = true;
    try {
      const config = await api('/catalog'); const group = byId('academicPaperProviders'); group.replaceChildren();
      config.providers.forEach((name) => {
        const label = document.createElement('label'); const box = document.createElement('input'); box.type = 'checkbox'; box.value = name; box.checked = config.default_providers.includes(name);
        label.append(box, document.createTextNode(labels[name] || name)); group.append(label);
      });
      note(config.retrieval === 'keyword_and_vector' ? '本地关键词＋向量检索已启用。' : '当前使用持久化关键词检索；本地向量模型未配置，不会自动调用云端Embedding。');
      if (config.warning) note('向量后端暂不可用，当前使用关键词检索；可查看配置说明。');
      await refresh();
    } catch (error) { loaded = false; note(error.message); }
  }
  function options() {
    if (editing) throw new Error('请等待知识库操作完成。');
    const mode = byId('academicSourceMode').value;
    const providers = mode === 'local' ? [] : [...byId('academicPaperProviders').querySelectorAll('input:checked')].map((node) => node.value);
    const ids = mode === 'web' ? [] : selectedDocuments();
    if (mode !== 'local' && (!providers.length || providers.length > 6)) throw new Error('请选择1–6个在线论文源。');
    if (mode !== 'web' && !ids.length) throw new Error('本地或混合研究需要勾选至少一个知识库文档。');
    if (ids.length > 64) throw new Error('一次最多选择64个本地文档。');
    return { source_mode: mode, paper_providers: providers, document_ids: ids };
  }
  function renderEvidence(data) {
    const panel = byId('academicEvidenceSources'); panel.replaceChildren(); panel.hidden = false;
    const heading = document.createElement('h3'); heading.textContent = '本次证据获取情况'; panel.append(heading);
    (data.sources || []).forEach((source) => {
      const row = document.createElement('p'); const label = document.createElement('span');
      label.textContent = `${source.title} — ${kinds[source.content_status] || '未知状态'} · ${source.provider} · ${(source.locations || []).join('；')} `;
      row.append(label); safeLink(row, '原文', source.url); panel.append(row);
    });
    if ((data.failures || []).length) {
      const warning = document.createElement('p'); warning.textContent = `本次未成功的来源：${data.failures.map((item) => labels[item.provider] || item.provider).join('、')}；没有用其他来源冒充。`; panel.append(warning);
    }
  }
  function policyNote(message) { byId('academicToolPolicyStatus').textContent = message; }
  function policyControls() {
    const locked = busy || editing || policyBusy;
    byId('academicToolPolicyPlan').disabled = locked || policyAvailable === false || !byId('academicToolPolicyTask').value.trim();
    byId('academicToolPolicyExecute').disabled = locked || !policyProposal;
    byId('academicToolPolicyCancel').disabled = locked || !policyProposal;
    byId('academicToolPolicyClear').disabled = locked || (!policySession && !byId('academicToolPolicyTask').value);
  }
  function currentPolicyScope() {
    const mode = byId('academicSourceMode').value;
    return JSON.stringify({ mode, providers: mode === 'local' ? [] : [...byId('academicPaperProviders').querySelectorAll('input:checked')].map((node) => node.value).sort(), document_ids: mode === 'web' ? [] : selectedDocuments().sort() });
  }
  function invalidatePolicy(scopeChanged = false) {
    const hadProposal = !!policyProposal;
    policyProposal = null; policyRevision += 1;
    const decision = byId('academicToolPolicyDecision'); decision.replaceChildren(); decision.hidden = true;
    if (scopeChanged) {
      policySession = null; byId('academicToolPolicyResults').replaceChildren();
      policyNote('来源范围已更新，请重新生成工具建议。');
    } else if (hadProposal) policyNote('任务已修改，原建议已失效，请重新生成。');
    controls();
  }
  function policyScopeChanged() {
    const next = currentPolicyScope();
    if (policyScopeKey && next !== policyScopeKey) invalidatePolicy(true);
    policyScopeKey = next;
  }
  async function policyAction(callback) {
    if (busy || editing || policyBusy) return;
    policyBusy = true; controls();
    try { await callback(); } catch (error) { policyNote(error.message); }
    finally { policyBusy = false; controls(); }
  }
  function policyPost(path, body) {
    return api(`/tool-policy/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }
  async function checkPolicy() {
    const model = byId('academicToolPolicyModel'); model.textContent = '正在检查本机模型状态…';
    try {
      const status = await api('/tool-policy/status');
      policyAvailable = status.available === true;
      model.textContent = policyAvailable ? '本机模型已就绪；生成建议只调用本机模型。' : '本机模型尚不可用。请启动配置好的 llama.cpp 与 SFT v1 模型后重新检查。';
    } catch (error) {
      policyAvailable = false; model.textContent = '无法确认本机模型状态；未运行推理，也未执行检索。';
      throw error;
    }
  }
  function policyText(container, value, tag = 'p') {
    const node = document.createElement(tag); node.textContent = value; container.append(node); return node;
  }
  function renderPolicyPapers(container, papers) {
    (Array.isArray(papers) ? papers : []).forEach((paper) => {
      const article = document.createElement('article');
      policyText(article, paper.title || '未命名论文', 'strong');
      policyText(article, [paper.provider, paper.year, paper.doi ? `DOI: ${paper.doi}` : '', paper.paper_id ? `候选编号: ${paper.paper_id}` : ''].filter(Boolean).join(' · '));
      if (paper.abstract) policyText(article, `摘要：${paper.abstract}`);
      safeLink(article, '原文', paper.url); container.append(article);
    });
  }
  function renderPolicyResult(data) {
    const panel = byId('academicToolPolicyResults'); panel.replaceChildren();
    const result = data.result || {};
    if (result.tool) policyText(panel, `已执行：${result.tool}`, 'strong');
    const papers = Array.isArray(result.papers) ? result.papers : (!result.tool && Array.isArray(data.candidates) ? data.candidates : []);
    renderPolicyPapers(panel, papers);
    const readings = Array.isArray(result.readings) ? result.readings : [];
    readings.forEach((reading) => {
      renderPolicyPapers(panel, [reading]);
      policyText(panel, kinds[reading.status] || '未知正文状态');
      if (reading.truncated) policyText(panel, '本次仅返回部分原文片段。');
      (Array.isArray(reading.passages) ? reading.passages : []).forEach((passage) => {
        const article = document.createElement('article');
        policyText(article, [passage.title, passage.locator, passage.section, passage.page ? `第${passage.page}页` : ''].filter(Boolean).join(' · '), 'strong');
        policyText(article, passage.text || ''); panel.append(article);
      });
    });
    if (result.warning) policyText(panel, result.warning);
    if (!papers.length && !readings.length) policyText(panel, '本次没有返回可展示的论文或原文片段。');
  }
  async function planPolicy() {
    const task = byId('academicToolPolicyTask').value.trim(); if (!task) return;
    const scope = options();
    invalidatePolicy(); const revision = policyRevision;
    policyNote('正在由本机模型生成一步工具建议，尚未执行工具。CPU首次请求可能需要约1–3分钟，请勿重复点击…');
    const data = await policyPost('plan', { task, mode: scope.source_mode, providers: scope.paper_providers, document_ids: scope.document_ids, ...(policySession ? { session_id: policySession } : {}) });
    if (revision !== policyRevision) return;
    const decision = data.decision;
    if (!data.session_id || !decision || !['call', 'clarify', 'finish'].includes(decision.action)) throw new Error('本机建议格式无效，未执行任何工具。');
    policySession = data.session_id;
    const panel = byId('academicToolPolicyDecision'); panel.replaceChildren(); panel.hidden = false;
    if (decision.action === 'call') {
      if (!data.proposal_id || data.status !== 'awaiting_approval') throw new Error('本机建议缺少有效确认信息，未执行任何工具。');
      policyText(panel, `建议工具：${decision.tool}`, 'strong');
      policyText(panel, JSON.stringify(decision.arguments || {}, null, 2));
      policyProposal = { session_id: data.session_id, proposal_id: data.proposal_id, tool: decision.tool, scope: currentPolicyScope(), task };
      policyNote('请核对工具和参数，然后确认执行。确认仅适用于这一步。');
    } else {
      policyText(panel, decision.action === 'clarify' ? '需要补充任务信息' : '本轮工具任务结束', 'strong');
      policyText(panel, decision.reason || '请根据已有结果决定下一步。');
      policyNote(decision.action === 'clarify' ? '补充任务描述后可再次生成建议；未执行工具。' : '没有待执行工具；可输入后续任务继续。');
    }
    if ((data.candidates || []).length && !byId('academicToolPolicyResults').childNodes.length) renderPolicyResult({ candidates: data.candidates });
  }
  async function executePolicy(approve) {
    const proposal = policyProposal; if (!proposal) return;
    if (proposal.scope !== currentPolicyScope() || proposal.task !== byId('academicToolPolicyTask').value.trim()) {
      invalidatePolicy(true); return;
    }
    if (approve && ['search_papers', 'resolve_paper', 'read_paper'].includes(proposal.tool) && !window.confirm('此工具会访问所选在线论文源或候选论文网站。确认执行这一步检索或读取？')) return;
    policyProposal = null; controls();
    policyNote(approve ? '正在执行已确认的工具…' : '正在取消本次建议…');
    const data = await policyPost('execute', { session_id: proposal.session_id, proposal_id: proposal.proposal_id, approve });
    if (approve && data.status === 'executed') {
      renderPolicyResult(data); policyNote('工具已执行。查看结果后，可修改任务或再次生成下一步建议。');
    } else if (!approve && data.status === 'cancelled') {
      byId('academicToolPolicyDecision').replaceChildren(); byId('academicToolPolicyDecision').hidden = true;
      policyNote('建议已取消，未执行工具。');
    } else throw new Error('未收到有效的工具执行状态；本次建议已失效，未自动重试。');
  }
  function clearPolicy() {
    policySession = null; invalidatePolicy();
    byId('academicToolPolicyTask').value = ''; byId('academicToolPolicyResults').replaceChildren();
    policyNote('工具会话已清空。输入新任务后可重新生成建议。'); controls();
  }
  function setup() {
    policyScopeKey = currentPolicyScope();
    byId('academicToolPolicyPanel').addEventListener('toggle', () => {
      if (byId('academicToolPolicyPanel').open && policyAvailable === null) policyAction(checkPolicy);
    });
    byId('academicToolPolicyCheck').addEventListener('click', () => policyAction(checkPolicy));
    byId('academicToolPolicyTask').addEventListener('input', () => invalidatePolicy());
    ['academicSourceMode', 'academicPaperProviders', 'academicLibraryDocuments'].forEach((id) => byId(id).addEventListener('change', policyScopeChanged));
    byId('academicToolPolicyPlan').addEventListener('click', () => policyAction(planPolicy));
    byId('academicToolPolicyExecute').addEventListener('click', () => policyAction(() => executePolicy(true)));
    byId('academicToolPolicyCancel').addEventListener('click', () => policyAction(() => executePolicy(false)));
    byId('academicToolPolicyClear').addEventListener('click', () => policyAction(clearPolicy));
    byId('academicLibraryRefresh').addEventListener('click', () => action(async () => { loaded = false; await load(); }));
    byId('academicLibraryUpload').addEventListener('change', (event) => action(async () => {
      const files = [...event.target.files];
      for (const file of files) {
        if (file.size > 12000000) throw new Error('单文件上限12MB。');
        const form = new FormData(); form.append('file', file); await api('/documents', { method: 'POST', body: form });
      }
      event.target.value = ''; await refresh(); note('导入完成；可勾选文档进行原文片段检索。');
    }));
    byId('academicLibrarySearch').addEventListener('click', () => action(async () => {
      const query = byId('academicLibraryQuery').value.trim(); if (!query) return;
      const result = await api('/search', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, document_ids: selectedDocuments() }) });
      renderPassages(result.results || []);
    }));
  }
  window.academicLibrary = { setup, load, options, renderEvidence, setBusy(value) { busy = !!value; controls(); } };
})();
