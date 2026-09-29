(() => {
  'use strict';

  const HISTORY_KEY = 'conversationHistory';
  const ACADEMIC_SESSION_KEY = 'gptResearcherAcademicSessionV2';
  const MAX_HISTORY_ENTRIES = 40;
  const MAX_ACTIVITY_ENTRIES = 240;
  const RECONNECT_LIMIT = 5;
  const ACADEMIC_STAGES = [
    'planning', 'evidence', 'outline', 'approval', 'writing', 'review', 'human_review',
  ];

  const MODE_HINTS = {
    research_report: '适合快速了解主题，自动检索并生成带引用摘要。',
    detailed_report: '适合需要更完整论证、更多上下文与章节结构的研究。',
    resource_report: '重点汇总资源、链接与可继续阅读的材料。',
    deep: '使用更深入的研究过程，耗时与调用成本通常更高。',
    academic_langgraph: '固定中文学术结构，包含目录审批、引用复核和可控修订。',
  };

  const ACADEMIC_ERROR_MESSAGES = {
    invalid_request: '请求格式不符合工作流合同，请刷新页面后重试。',
    invalid_session: '会话标识无效，请开始一个新的学术任务。',
    session_not_found: '当前学术会话已失效，服务重启后需要重新开始。',
    session_busy: '当前会话仍在处理上一项操作，请稍后再试。',
    start_failed: '学术工作流启动失败。请检查模型与检索器配置后重新开始。',
    decision_already_submitted: '这份目录已经处理，无法重复提交决定。',
    decision_failed: '批准后的写作或引用审核未完成，当前目录仍可用于排查。',
    terminal_invalid: '工作流返回了无法发布的终态。',
    invalid_sections: '章节选择无效，请重新选择 1–10 个章节。',
    invalid_feedback: '修改意见不完整或超出长度限制。',
    revision_unavailable: '当前会话没有可修订的草稿。',
    revision_already_used: '旧版一次性修订已使用，请使用带意见的定向修订。',
    revision_pending: '已有候选稿等待采用或放弃，请先处理候选版本。',
    revision_request_conflict: '修订请求标识与之前的内容冲突，请重新提交。',
    revision_failed: '本次修订失败，当前采用稿未被覆盖；可检查意见后手动重试。',
    revision_decision_failed: '候选版本决定未保存，候选稿仍保留，可再次确认。',
    stale_version: '页面中的版本已过期，正在同步服务器上的最新草稿状态。',
    export_unavailable: '当前状态还不能导出最终报告。',
    export_failed: '报告导出失败，当前草稿未丢失。',
    artifact_failed: '报告产物路径无效或生成失败。',
    composer_evidence_unavailable: '当前证据不足以进入学术写作。',
    citation_plan_invalid: '引用计划未通过一致性检查。',
    citation_review_result_invalid: '引用审核结果不完整。',
    state_invalid: '工作流保存状态不完整。',
    internal_error: '服务器发生内部错误，请查看本地服务日志。',
  };

  const MCP_PRESETS = {
    github: {
      name: 'github',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-github'],
      env: { GITHUB_PERSONAL_ACCESS_TOKEN: '${GITHUB_TOKEN}' },
    },
    tavily: {
      name: 'tavily',
      command: 'npx',
      args: ['-y', 'tavily-mcp@latest'],
      env: { TAVILY_API_KEY: '${TAVILY_API_KEY}' },
    },
    filesystem: {
      name: 'filesystem',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', '${WORKSPACE_PATH}'],
    },
  };

  const DOWNLOAD_IDS = {
    md: ['downloadLinkMdTop', 'downloadLinkMd'],
    pdf: ['downloadLinkTop', 'downloadLink'],
    docx: ['downloadLinkWordTop', 'downloadLinkWord'],
    json: ['downloadLinkJsonTop', 'downloadLinkJson'],
  };

  const REQUIRED_IDS = [
    'researchForm', 'task', 'taskCount', 'report_type', 'modeHint',
    'academicProfileOptions', 'academicProfile', 'advancedSettings', 'tone',
    'report_source', 'maxSearchResults', 'queryDomains', 'mcpEnabled',
    'mcpConfigSection', 'mcpConfig', 'mcpConfigStatus', 'submitButton',
    'workspaceSubtitle', 'runState', 'researchStatus', 'emptyState',
    'academicWorkflowPanel', 'academicStages', 'academicOutlinePanel',
    'academicOutlineTitle', 'academicOutlineSections', 'academicApprove',
    'academicReject', 'academicReviewPanel', 'academicReviewSections',
    'academicArtifactLinks', 'academicRevisionPanel', 'academicVersionLabel',
    'academicGlobalFeedback', 'academicEditableSections', 'academicRevisionNotice',
    'academicRevise', 'academicExport', 'academicRevisionPreview',
    'academicCandidateStatus', 'academicRevisionComparison',
    'academicAcceptRevision', 'academicDiscardRevision', 'academicVersionHistory',
    'academicFinalPanel', 'academicFinalLinks', 'academicContinueRevision',
    'progressCard', 'modernSpinner', 'output', 'selectedImagesContainer',
    'reportCard', 'reportContainer', 'reportActions', 'status', 'chatContainer',
    'chatMessages', 'chatInput', 'sendChatBtn', 'websocketPanel', 'historyPanel',
    'drawerScrim', 'stickyDownloadsBar', 'toastRegion',
  ];

  const dom = {};
  const state = {
    socket: null,
    socketGeneration: 0,
    outbox: [],
    connection: {
      status: 'idle',
      startedAt: null,
      lastActivityAt: null,
      attempts: 0,
      messages: 0,
      reconnects: 0,
      reconnectTimer: null,
      monitorTimer: null,
    },
    run: { active: false, kind: null, status: 'idle', task: '' },
    report: { markdown: '', links: {} },
    academic: {
      sessionId: null,
      stage: null,
      busy: false,
      outline: null,
      workspace: null,
      exported: false,
      recovering: false,
      recoveryAttempts: 0,
      lastAction: null,
    },
    history: [],
    chat: { messages: [], waiting: false },
  };

  let markdownConverter = null;
  let lastFocusedDrawerTrigger = null;
  let speechRecognition = null;

  function byId(id) { return document.getElementById(id); }
  function text(value) { return typeof value === 'string' ? value : ''; }
  function asArray(value) { return Array.isArray(value) ? value : []; }

  function collectDom() {
    REQUIRED_IDS.forEach((id) => {
      const element = byId(id);
      if (!element) throw new Error(`Frontend contract element missing: #${id}`);
      dom[id] = element;
    });
  }

  function formatClock(timestamp) {
    if (!timestamp) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(new Date(timestamp));
  }

  function formatDuration(seconds) {
    const safe = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const rest = safe % 60;
    if (hours) return `${hours}h ${minutes}m ${rest}s`;
    if (minutes) return `${minutes}m ${rest}s`;
    return `${rest}s`;
  }

  function showToast(message, kind = 'info', duration = 3600) {
    const toast = document.createElement('div');
    toast.className = `toast${kind === 'error' ? ' error' : ''}`;
    toast.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    toast.textContent = text(message);
    dom.toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), duration);
  }

  function createMarkdownConverter() {
    if (!window.showdown || typeof window.showdown.Converter !== 'function') return null;
    return new window.showdown.Converter({
      ghCodeBlocks: true,
      tables: true,
      tasklists: true,
      smartIndentationFix: true,
      simpleLineBreaks: true,
      literalMidWordUnderscores: true,
      openLinksInNewWindow: true,
    });
  }

  function hardenLinks(root) {
    root.querySelectorAll('a').forEach((anchor) => {
      const raw = anchor.getAttribute('href') || '';
      let parsed;
      try { parsed = new URL(raw, window.location.origin); } catch (_) {
        anchor.removeAttribute('href');
        return;
      }
      if (!['http:', 'https:'].includes(parsed.protocol)) {
        anchor.removeAttribute('href');
        return;
      }
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
    });
  }

  function markdownFragment(markdown) {
    const fragment = document.createElement('div');
    const source = text(markdown);
    if (!markdownConverter || !window.DOMPurify || typeof window.DOMPurify.sanitize !== 'function') {
      fragment.textContent = source;
      fragment.style.whiteSpace = 'pre-wrap';
      return fragment;
    }
    fragment.innerHTML = window.DOMPurify.sanitize(markdownConverter.makeHtml(source), {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ['style', 'script', 'iframe', 'object', 'embed', 'form'],
      FORBID_ATTR: ['style', 'srcdoc'],
    });
    hardenLinks(fragment);
    return fragment;
  }

  function renderMarkdownInto(container, markdown) {
    container.replaceChildren(...markdownFragment(markdown).childNodes);
  }

  function normalizeOutputUrl(value, academicOnly = false) {
    if (typeof value !== 'string' || !value.trim()) return null;
    const raw = value.trim().replaceAll('\\', '/');
    let parsed;
    try { parsed = new URL(raw, window.location.origin); } catch (_) { return null; }
    if (parsed.origin !== window.location.origin || parsed.protocol !== window.location.protocol) return null;
    const prefix = academicOnly ? '/outputs/academic/' : '/outputs/';
    if (!parsed.pathname.startsWith(prefix)) return null;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  }

  function setRunState(status, label, subtitle) {
    state.run.status = status;
    const visual = status === 'error'
      ? 'error'
      : ['ready', 'finished', 'review_required', 'exported', 'rejected'].includes(status)
        ? 'finished'
        : status === 'idle' ? 'idle' : 'running';
    dom.runState.dataset.state = visual;
    dom.researchStatus.textContent = label || {
      idle: '空闲', connecting: '连接中', running: '研究中', awaiting_outline: '等待审批',
      writing: '写作中', reviewing: '复核中', review_required: '需要人工确认',
      ready: '报告就绪', exported: '已导出', finished: '已完成', rejected: '已结束', error: '需要处理',
    }[status] || status;
    dom.workspaceSubtitle.textContent = subtitle || (state.run.task || '等待新的研究任务');
    dom.emptyState.hidden = status !== 'idle';
    dom.progressCard.hidden = status === 'idle';
    const spinning = ['connecting', 'running', 'writing', 'reviewing'].includes(status);
    dom.modernSpinner.hidden = !spinning;
    dom.modernSpinner.classList.toggle('spinning', spinning);
    dom.progressCard.setAttribute('aria-busy', String(spinning));
    renderBusyControls();
  }

  function renderBusyControls() {
    const busy = state.run.active || state.academic.busy;
    const outlineActionable = state.run.status === 'awaiting_outline'
      && !dom.academicOutlinePanel.hidden
      && Boolean(state.academic.sessionId);
    dom.submitButton.disabled = busy;
    dom.academicApprove.disabled = state.academic.busy || !outlineActionable;
    dom.academicReject.disabled = state.academic.busy || !outlineActionable;
    const workspace = state.academic.workspace;
    const pending = Boolean(workspace && workspace.pending);
    dom.academicRevise.disabled = state.academic.busy || pending || !workspace;
    dom.academicExport.disabled = state.academic.busy || pending || !workspace;
    dom.academicAcceptRevision.disabled = state.academic.busy || !pending;
    dom.academicDiscardRevision.disabled = state.academic.busy || !pending;
    dom.academicEditableSections.querySelectorAll('input, textarea').forEach((control) => {
      control.disabled = state.academic.busy || pending;
    });
  }

  function resetDownloads() {
    Object.values(DOWNLOAD_IDS).flat().forEach((id) => {
      const anchor = byId(id);
      if (!anchor) return;
      anchor.href = '#';
      anchor.classList.add('disabled');
      anchor.setAttribute('aria-disabled', 'true');
      anchor.tabIndex = -1;
    });
    ['copyToClipboard', 'copyToClipboardTop'].forEach((id) => {
      const button = byId(id);
      button.disabled = true;
      button.classList.add('disabled');
    });
    dom.stickyDownloadsBar.hidden = true;
    dom.reportActions.hidden = true;
    byId('jsonButtonContainer').hidden = true;
    state.report.links = {};
  }

  function setDownloadLink(id, url) {
    const anchor = byId(id);
    if (!anchor) return;
    if (!url) {
      anchor.href = '#';
      anchor.classList.add('disabled');
      anchor.setAttribute('aria-disabled', 'true');
      anchor.tabIndex = -1;
      return;
    }
    anchor.href = url;
    anchor.classList.remove('disabled');
    anchor.setAttribute('aria-disabled', 'false');
    anchor.removeAttribute('tabindex');
  }

  function updateDownloads(rawLinks, academicOnly = false) {
    const links = {};
    Object.keys(DOWNLOAD_IDS).forEach((format) => {
      const url = normalizeOutputUrl(rawLinks && rawLinks[format], academicOnly);
      if (url) links[format] = url;
      DOWNLOAD_IDS[format].forEach((id) => setDownloadLink(id, url));
    });
    state.report.links = links;
    const available = Object.keys(links).length > 0;
    dom.stickyDownloadsBar.hidden = !available;
    dom.reportActions.hidden = !available;
    byId('jsonButtonContainer').hidden = !links.json;
    ['copyToClipboard', 'copyToClipboardTop'].forEach((id) => {
      const button = byId(id);
      button.disabled = !state.report.markdown;
      button.classList.toggle('disabled', !state.report.markdown);
    });
    return links;
  }

  function renderReport(markdown) {
    state.report.markdown = text(markdown);
    renderMarkdownInto(dom.reportContainer, state.report.markdown);
    dom.reportCard.hidden = false;
    ['copyToClipboard', 'copyToClipboardTop'].forEach((id) => {
      const button = byId(id);
      button.disabled = !state.report.markdown;
      button.classList.toggle('disabled', !state.report.markdown);
    });
  }

  function appendActivity(title, message, tone = 'info') {
    const entry = document.createElement('div');
    entry.className = `agent_response ${tone}`;
    const heading = document.createElement('strong');
    heading.textContent = text(title) || '更新';
    const body = document.createElement('p');
    body.textContent = text(message);
    const time = document.createElement('time');
    time.dateTime = new Date().toISOString();
    time.textContent = formatClock(Date.now());
    entry.append(heading, body, time);
    dom.output.append(entry);
    while (dom.output.children.length > MAX_ACTIVITY_ENTRIES) dom.output.firstElementChild.remove();
    dom.output.scrollTop = dom.output.scrollHeight;
    dom.progressCard.hidden = false;
  }

  function renderSubqueries(items) {
    const values = asArray(items).map((item) => text(item)).filter(Boolean);
    if (!values.length) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'sub-questions';
    const title = document.createElement('strong');
    title.className = 'sub-questions-heading';
    title.textContent = '研究子问题';
    const list = document.createElement('div');
    list.className = 'sub-questions-list';
    values.forEach((value) => {
      const pill = document.createElement('span');
      pill.className = 'sub-question-pill';
      pill.textContent = value;
      list.append(pill);
    });
    wrapper.append(title, list);
    dom.output.append(wrapper);
  }

  function clearRunView({ preserveAcademic = false } = {}) {
    dom.output.replaceChildren();
    dom.selectedImagesContainer.replaceChildren();
    dom.reportContainer.replaceChildren();
    dom.chatMessages.replaceChildren();
    dom.chatContainer.hidden = true;
    dom.reportCard.hidden = true;
    dom.selectedImagesContainer.hidden = true;
    dom.reportActions.hidden = true;
    dom.status.textContent = '';
    state.report.markdown = '';
    state.chat.messages = [];
    state.chat.waiting = false;
    resetDownloads();
    if (!preserveAcademic) resetAcademicView();
  }

  function resetAcademicView() {
    cancelReconnectTimer();
    state.academic.sessionId = null;
    state.academic.stage = null;
    state.academic.busy = false;
    state.academic.outline = null;
    state.academic.workspace = null;
    state.academic.exported = false;
    state.academic.recovering = false;
    state.academic.recoveryAttempts = 0;
    state.academic.lastAction = null;
    sessionStorage.removeItem(ACADEMIC_SESSION_KEY);
    dom.academicWorkflowPanel.hidden = true;
    dom.academicOutlinePanel.hidden = true;
    dom.academicReviewPanel.hidden = true;
    dom.academicRevisionPanel.hidden = true;
    dom.academicRevisionPreview.hidden = true;
    dom.academicFinalPanel.hidden = true;
    dom.academicContinueRevision.hidden = true;
    dom.academicOutlineSections.replaceChildren();
    dom.academicReviewSections.replaceChildren();
    dom.academicEditableSections.replaceChildren();
    dom.academicRevisionComparison.replaceChildren();
    dom.academicVersionHistory.replaceChildren();
    dom.academicArtifactLinks.replaceChildren();
    dom.academicFinalLinks.replaceChildren();
    dom.academicGlobalFeedback.value = '';
    dom.academicRevisionNotice.textContent = '';
    updateAcademicStage(null);
  }

  function persistAcademicSession() {
    if (!state.academic.sessionId) {
      sessionStorage.removeItem(ACADEMIC_SESSION_KEY);
      return;
    }
    try {
      sessionStorage.setItem(ACADEMIC_SESSION_KEY, JSON.stringify({
        sessionId: state.academic.sessionId,
        stage: state.academic.stage,
        outline: state.academic.outline,
        task: state.run.task,
        reportMode: dom.academicProfile.value,
      }));
    } catch (_) {
      // Storage availability must not break an active workflow.
    }
  }

  function restoreAcademicSession() {
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem(ACADEMIC_SESSION_KEY) || 'null'); } catch (_) {
      sessionStorage.removeItem(ACADEMIC_SESSION_KEY);
      return;
    }
    if (!saved || typeof saved.sessionId !== 'string') return;
    state.academic.sessionId = saved.sessionId;
    state.academic.stage = typeof saved.stage === 'string' ? saved.stage : 'human_review';
    state.academic.outline = saved.outline && typeof saved.outline === 'object' ? saved.outline : null;
    state.academic.busy = true;
    state.run.kind = 'academic';
    state.run.task = text(saved.task);
    if (state.run.task) { dom.task.value = state.run.task; updateTaskCount(); }
    dom.report_type.value = 'academic_langgraph';
    if ([...dom.academicProfile.options].some((option) => option.value === saved.reportMode)) dom.academicProfile.value = saved.reportMode;
    renderMode();
    dom.academicWorkflowPanel.hidden = false;
    if (state.academic.outline) renderAcademicOutline(state.academic.outline);
    updateAcademicStage(state.academic.stage);
    setRunState('connecting', '恢复会话', '正在读取服务器保存的草稿状态');
    state.academic.recovering = true;
    sendCommand('academic_draft_state', { session_id: state.academic.sessionId }, { fresh: true });
  }

  function updateAcademicStage(stage) {
    state.academic.stage = stage;
    const normalized = stage === 'complete' ? 'human_review' : stage;
    const activeIndex = ACADEMIC_STAGES.indexOf(normalized);
    dom.academicStages.querySelectorAll('[data-stage]').forEach((item) => {
      const index = ACADEMIC_STAGES.indexOf(item.dataset.stage);
      item.classList.toggle('active', activeIndex >= 0 && index === activeIndex && stage !== 'complete');
      item.classList.toggle('complete', activeIndex >= 0 && (index < activeIndex || stage === 'complete'));
    });
    persistAcademicSession();
  }

  function renderMode() {
    const reportType = byId('report_type').value;
    const academic = reportType === 'academic_langgraph';
    dom.task.maxLength = academic ? 4096 : 12000;
    dom.academicProfileOptions.hidden = !academic;
    dom.advancedSettings.hidden = academic;
    dom.modeHint.textContent = MODE_HINTS[reportType] || '';
    dom.submitButton.querySelector('span').textContent = academic ? '启动学术工作流' : '开始研究';
    updateTaskCount();
  }

  function updateTaskCount() {
    dom.taskCount.textContent = `${dom.task.value.length} / ${dom.task.maxLength}`;
  }

  function renderConnection() {
    const connection = state.connection;
    const socket = state.socket;
    const readyState = socket ? socket.readyState : WebSocket.CLOSED;
    const labels = { idle: '按需连接', connecting: '正在连接', open: '已连接', closed: '已断开', failed: '连接失败' };
    byId('connectionStatus').textContent = labels[connection.status] || connection.status;
    byId('readyState').textContent = ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][readyState] || 'CLOSED';
    byId('connectionAttempts').textContent = String(connection.attempts);
    byId('messagesReceived').textContent = String(connection.messages);
    byId('currentTask').textContent = state.run.task || '—';
    byId('lastActivity').textContent = formatClock(connection.lastActivityAt);
    document.querySelectorAll('.status-dot').forEach((dot) => {
      dot.classList.toggle('connected', connection.status === 'open');
      dot.classList.toggle('failed', connection.status === 'failed' || connection.status === 'closed');
    });
    if (!connection.monitorTimer) {
      connection.monitorTimer = window.setInterval(() => {
        byId('connectionDuration').textContent = connection.startedAt
          ? formatDuration((Date.now() - connection.startedAt) / 1000) : '—';
        byId('lastActivity').textContent = formatClock(connection.lastActivityAt);
      }, 1000);
    }
  }

  function websocketUrl() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws`;
  }

  function cancelReconnectTimer() {
    if (!state.connection.reconnectTimer) return;
    window.clearTimeout(state.connection.reconnectTimer);
    state.connection.reconnectTimer = null;
  }

  function closeSocket({ clearQueue = true } = {}) {
    cancelReconnectTimer();
    state.socketGeneration += 1;
    if (clearQueue) state.outbox = [];
    if (!state.socket) return;
    state.socket.__intentionalClose = true;
    if (![WebSocket.CLOSED, WebSocket.CLOSING].includes(state.socket.readyState)) state.socket.close();
    state.socket = null;
    state.connection.startedAt = null;
    state.connection.status = 'idle';
    renderConnection();
  }

  function createSocket() {
    const generation = ++state.socketGeneration;
    const socket = new WebSocket(websocketUrl());
    socket.__intentionalClose = false;
    state.socket = socket;
    state.connection.status = 'connecting';
    state.connection.attempts += 1;
    renderConnection();
    socket.addEventListener('open', () => {
      if (generation !== state.socketGeneration) return;
      state.connection.status = 'open';
      state.connection.startedAt = Date.now();
      state.connection.lastActivityAt = Date.now();
      state.connection.reconnects = 0;
      state.outbox.splice(0).forEach((message) => socket.send(message));
      renderConnection();
    });
    socket.addEventListener('message', (event) => {
      if (generation !== state.socketGeneration) return;
      state.connection.messages += 1;
      state.connection.lastActivityAt = Date.now();
      renderConnection();
      if (event.data === 'pong') return;
      let data;
      try { data = JSON.parse(event.data); } catch (_) {
        appendActivity('连接消息', '收到无法解析的服务器消息，已安全忽略。', 'warning');
        return;
      }
      dispatchMessage(data);
    });
    socket.addEventListener('error', () => {
      if (generation !== state.socketGeneration) return;
      state.connection.status = 'failed';
      renderConnection();
    });
    socket.addEventListener('close', () => {
      if (generation !== state.socketGeneration || socket.__intentionalClose) return;
      state.socket = null;
      state.connection.startedAt = null;
      state.connection.status = 'closed';
      state.outbox = [];
      renderConnection();
      if (state.run.kind === 'academic' && state.academic.sessionId) scheduleAcademicRecovery();
      else if (state.run.active) {
        state.run.active = false;
        setRunState('error', '连接中断', '普通研究不会自动重放，避免重复调用；请手动重新开始。');
        appendActivity('连接中断', '为避免重复调用，未自动重放普通研究请求。', 'error');
      }
    });
    return socket;
  }

  function sendCommand(command, payload, { fresh = false } = {}) {
    const message = `${command} ${JSON.stringify(payload)}`;
    if (fresh) closeSocket();
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
      state.socket.send(message);
      state.connection.lastActivityAt = Date.now();
      renderConnection();
      return;
    }
    state.outbox.push(message);
    if (!state.socket || state.socket.readyState > WebSocket.CONNECTING) createSocket();
  }

  function scheduleAcademicRecovery() {
    if (!state.academic.sessionId) return;
    state.academic.recovering = true;
    state.academic.busy = true;
    state.run.active = true;
    if (state.connection.reconnectTimer) { renderBusyControls(); return; }
    if (state.academic.recoveryAttempts >= RECONNECT_LIMIT) {
      state.academic.busy = false;
      state.run.active = false;
      setRunState('error', '恢复失败', '无法同步学术会话；服务器可能已经重启。');
      return;
    }
    const delay = Math.min(8000, 900 * (2 ** state.academic.recoveryAttempts));
    const sessionId = state.academic.sessionId;
    state.academic.recoveryAttempts += 1;
    state.connection.reconnectTimer = window.setTimeout(() => {
      state.connection.reconnectTimer = null;
      if (!state.academic.recovering || state.academic.sessionId !== sessionId || state.run.kind !== 'academic') return;
      sendCommand('academic_draft_state', { session_id: sessionId }, { fresh: true });
    }, delay);
    setRunState('connecting', '恢复会话', '连接中断，正在读取已保存的草稿；不会重放付费操作。');
  }

  function prepareNewRun(kind, task) {
    closeSocket();
    clearRunView();
    state.run.active = true;
    state.run.kind = kind;
    state.run.task = task;
    state.connection.messages = 0;
    byId('currentTask').textContent = task;
    setRunState('connecting', '准备连接', task);
  }

  function validateMcpConfig() {
    const status = dom.mcpConfigStatus;
    try {
      const value = JSON.parse(dom.mcpConfig.value.trim() || '[]');
      if (!Array.isArray(value)) throw new Error('配置必须是 JSON 数组');
      value.forEach((server, index) => {
        if (!server || typeof server !== 'object' || Array.isArray(server)) throw new Error(`第 ${index + 1} 项必须是对象`);
        if (typeof server.name !== 'string' || !server.name.trim()) throw new Error(`第 ${index + 1} 项缺少 name`);
        if (typeof server.command !== 'string' || !server.command.trim()) throw new Error(`第 ${index + 1} 项缺少 command`);
        if (server.args !== undefined && !Array.isArray(server.args)) throw new Error(`第 ${index + 1} 项 args 必须是数组`);
        if (server.env !== undefined && (!server.env || typeof server.env !== 'object' || Array.isArray(server.env))) throw new Error(`第 ${index + 1} 项 env 必须是对象`);
      });
      status.textContent = `有效 JSON · ${value.length} 个 server`;
      status.dataset.state = 'valid';
      return value;
    } catch (error) {
      status.textContent = `无效配置 · ${error.message}`;
      status.dataset.state = 'invalid';
      return null;
    }
  }

  function buildGeneralPayload(task) {
    const mcpEnabled = byId('mcpEnabled').checked;
    const mcpConfigs = mcpEnabled ? validateMcpConfig() : [];
    if (mcpEnabled && !mcpConfigs) throw new Error('请先修正 MCP JSON 配置');
    const maxResults = Number.parseInt(byId('maxSearchResults').value, 10);
    return {
      task,
      report_type: byId('report_type').value,
      source_urls: [], document_urls: [], tone: byId('tone').value, headers: {},
      report_source: byId('report_source').value,
      query_domains: byId('queryDomains').value.split(',').map((item) => item.trim()).filter(Boolean),
      mcp_enabled: mcpEnabled, mcp_strategy: 'fast', mcp_configs: mcpConfigs || [],
      max_search_results: Number.isInteger(maxResults) ? Math.min(20, Math.max(1, maxResults)) : 5,
    };
  }

  function handleResearchSubmit(event) {
    event.preventDefault();
    if (state.run.active || state.academic.busy) {
      showToast('当前任务仍在运行，请等待完成。', 'error');
      return;
    }
    const task = byId('task').value.trim();
    if (!task) { byId('task').focus(); showToast('请先输入研究问题。', 'error'); return; }
    const academic = byId('report_type').value === 'academic_langgraph';
    if (academic && task.length > 4096) {
      byId('task').focus();
      showToast('Academic 研究问题最多 4096 个字符，请精简后再提交。', 'error');
      return;
    }
    prepareNewRun(academic ? 'academic' : 'general', task);
    if (academic) {
      dom.academicWorkflowPanel.hidden = false;
      state.academic.busy = true;
      updateAcademicStage('planning');
      appendActivity('学术工作流', '正在规划研究问题与证据检索。');
      sendCommand('academic_start', { query: task, report_mode: byId('academicProfile').value }, { fresh: true });
      return;
    }
    let payload;
    try { payload = buildGeneralPayload(task); } catch (error) {
      state.run.active = false;
      setRunState('error', '配置无效', error.message);
      showToast(error.message, 'error');
      return;
    }
    appendActivity('研究已排队', '连接本地研究服务并准备检索。');
    sendCommand('start', payload, { fresh: true });
  }

  function dispatchMessage(data) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return;
    if (typeof data.type === 'string' && data.type.startsWith('academic_')) { handleAcademicMessage(data); return; }
    switch (data.type) {
      case 'logs': {
        const output = text(data.output);
        const tone = data.content === 'error' ? 'error' : data.content === 'warning' ? 'warning' : 'info';
        if (data.content === 'subqueries') renderSubqueries(data.metadata);
        appendActivity(text(data.content) || '研究进度', output, tone);
        state.run.active = data.content !== 'error';
        setRunState(data.content === 'error' ? 'error' : 'running', data.content === 'error' ? '运行失败' : '研究中');
        break;
      }
      case 'report':
        state.report.markdown += text(data.output);
        renderReport(state.report.markdown);
        setRunState('running', '生成报告');
        break;
      case 'path': finishGeneralResearch(data.output); break;
      case 'images': renderImages(data); break;
      case 'cost': renderCost(data.data); break;
      case 'chat':
        state.chat.waiting = false;
        removeChatLoading();
        addChatMessage(text(data.content) || '未收到可显示的回复。', 'assistant');
        break;
      case 'error':
        state.run.active = false;
        setRunState('error', '请求失败', text(data.output) || '服务器无法识别该请求。');
        appendActivity('服务器错误', text(data.output) || 'Unknown command', 'error');
        break;
      default:
        break;
    }
  }

  function finishGeneralResearch(rawLinks) {
    state.run.active = false;
    setRunState('finished', '研究完成', state.run.task);
    const links = updateDownloads(rawLinks || {});
    dom.status.textContent = '报告与下载文件已生成。';
    dom.reportActions.hidden = false;
    dom.chatContainer.hidden = false;
    if (!dom.chatMessages.children.length) addChatMessage('报告已经生成。你可以继续追问报告中的结论、来源或方法。', 'assistant', { persist: false });
    if (state.report.markdown) saveHistoryEntry({
      prompt: state.run.task, report: state.report.markdown, links,
      reportType: byId('report_type').value, reportSource: byId('report_source').value,
      tone: byId('tone').value, queryDomains: byId('queryDomains').value,
    });
  }

  function renderCost(cost) {
    if (!cost || typeof cost !== 'object') return;
    const summary = [cost.total_tokens ? `${cost.total_tokens} tokens` : '', cost.total_cost ? `${cost.total_cost}` : ''].filter(Boolean).join(' · ');
    if (summary) appendActivity('用量', summary);
  }

  function renderImages(data) {
    let images = data.data || data.output || [];
    if (typeof images === 'string') { try { images = JSON.parse(images); } catch (_) { images = []; } }
    dom.selectedImagesContainer.replaceChildren();
    asArray(images).slice(0, 24).forEach((item) => {
      const raw = typeof item === 'string' ? item : text(item.url || item.src);
      let url;
      try { url = new URL(raw, window.location.origin); } catch (_) { return; }
      if (!['http:', 'https:'].includes(url.protocol)) return;
      const image = document.createElement('img');
      image.src = url.href;
      image.alt = typeof item === 'object' ? text(item.alt || item.title) : '研究图片';
      image.loading = 'lazy';
      image.referrerPolicy = 'no-referrer';
      image.addEventListener('click', () => showImageDialog(image.src, image.alt));
      dom.selectedImagesContainer.append(image);
    });
    dom.selectedImagesContainer.hidden = !dom.selectedImagesContainer.children.length;
  }

  function showImageDialog(src, alt) {
    const dialog = document.createElement('div');
    dialog.className = 'image-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    const image = document.createElement('img'); image.src = src; image.alt = alt;
    dialog.append(image);
    dialog.addEventListener('click', () => dialog.remove());
    document.body.append(dialog);
  }

  function renderAcademicOutline(payload) {
    const sections = asArray(payload.sections);
    state.academic.outline = {
      session_id: text(payload.session_id) || state.academic.sessionId,
      title: text(payload.title),
      sections: sections.map((section) => ({ section_id: text(section.section_id), section_role: text(section.section_role), title: text(section.title) })),
    };
    dom.academicOutlineTitle.textContent = state.academic.outline.title || '研究目录';
    dom.academicOutlineSections.replaceChildren();
    state.academic.outline.sections.forEach((section) => {
      const item = document.createElement('li');
      const title = document.createElement('strong'); title.textContent = section.title;
      const meta = document.createElement('small'); meta.textContent = `${section.section_id} · ${section.section_role}`;
      item.append(title, meta); dom.academicOutlineSections.append(item);
    });
    dom.academicOutlinePanel.hidden = false;
    dom.academicReviewPanel.hidden = true;
    dom.academicFinalPanel.hidden = true;
    dom.academicWorkflowPanel.hidden = false;
    persistAcademicSession();
  }

  function addArtifactLink(container, label, value) {
    const url = normalizeOutputUrl(value, true);
    if (!url) return;
    const anchor = document.createElement('a');
    anchor.href = url; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; anchor.textContent = label;
    container.append(anchor);
  }

  function renderReviewMessage(data) {
    const workspace = data.draft_workspace;
    const details = new Map(asArray(workspace && workspace.sections).map((item) => [item.section_id, item]));
    dom.academicReviewSections.replaceChildren();
    asArray(data.reviews).forEach((review) => {
      const full = details.get(review.section_id) || {};
      const card = document.createElement('section'); card.className = 'academic-review-item';
      const heading = document.createElement('h4'); heading.textContent = text(full.title) || text(review.section_id) || '未命名章节';
      const verdict = document.createElement('span');
      verdict.className = `status-kicker ${review.verdict === 'supported' ? 'success' : 'warning'}`;
      verdict.textContent = text(review.verdict) || 'uncertain';
      const issues = document.createElement('p');
      issues.textContent = asArray(review.issues).length ? `问题：${asArray(review.issues).join('、')}` : '未返回具体问题标签。';
      const rationale = document.createElement('p');
      rationale.textContent = text(full.rationale) || text(review.caveat) || '机器审核意见仅供人工复核。';
      card.append(verdict, heading, issues, rationale); dom.academicReviewSections.append(card);
    });
    dom.academicArtifactLinks.replaceChildren();
    addArtifactLink(dom.academicArtifactLinks, '草稿 Markdown', data.draft_url);
    addArtifactLink(dom.academicArtifactLinks, 'Citation audit', data.audit_markdown_url);
    addArtifactLink(dom.academicArtifactLinks, 'Audit JSON', data.audit_json_url);
    dom.academicReviewPanel.hidden = false;
  }

  function renderWorkspaceReview(workspace) {
    if (!workspace || typeof workspace !== 'object') return;
    dom.academicReviewSections.replaceChildren();
    asArray(workspace.sections).forEach((section) => {
      const card = document.createElement('section'); card.className = 'academic-review-item';
      const heading = document.createElement('h4'); heading.textContent = text(section.title) || text(section.section_id) || '未命名章节';
      const verdict = document.createElement('span');
      verdict.className = `status-kicker ${section.verdict === 'supported' ? 'success' : 'warning'}`;
      verdict.textContent = text(section.verdict) || 'uncertain';
      const issues = document.createElement('p');
      issues.textContent = asArray(section.issues).length ? `问题：${asArray(section.issues).join('、')}` : '问题：无';
      const rationale = document.createElement('p'); rationale.textContent = text(section.rationale) || '机器审核意见仅供人工复核。';
      card.append(verdict, heading, issues, rationale); dom.academicReviewSections.append(card);
    });
    dom.academicArtifactLinks.replaceChildren();
    const current = asArray(workspace.versions).find((item) => item.version === workspace.current_version);
    if (current) {
      addArtifactLink(dom.academicArtifactLinks, `v${workspace.current_version} 草稿`, current.draft_url);
      addArtifactLink(dom.academicArtifactLinks, `v${workspace.current_version} Citation audit`, current.audit_url);
    }
    dom.academicReviewPanel.hidden = Boolean(workspace.machine_ready);
  }

  function captureEditorState() {
    const selected = new Set(); const feedback = new Map();
    dom.academicEditableSections.querySelectorAll('[data-section-id]').forEach((control) => {
      const sectionId = control.dataset.sectionId;
      if (control.matches('input[type="checkbox"]') && control.checked) selected.add(sectionId);
      if (control.matches('textarea')) feedback.set(sectionId, control.value);
    });
    return { selected, feedback, global: dom.academicGlobalFeedback.value };
  }

  function verdictLabel(value) {
    return { supported: '证据支持', uncertain: '仍需确认', unsupported: '证据不足' }[value] || text(value) || '未审查';
  }

  function renderDraftWorkspace(workspace, { preserveInputs = true } = {}) {
    if (!workspace || typeof workspace !== 'object') return;
    const saved = preserveInputs ? captureEditorState() : { selected: new Set(), feedback: new Map(), global: '' };
    state.academic.workspace = workspace;
    state.academic.sessionId = text(workspace.session_id) || state.academic.sessionId;
    dom.academicVersionLabel.textContent = `v${workspace.current_version} · ${workspace.machine_ready ? '机器审核通过' : '需要人工复核'}`;
    dom.academicEditableSections.replaceChildren();
    asArray(workspace.sections).forEach((section, index) => {
      const card = document.createElement('section'); card.className = 'academic-editor-section';
      const header = document.createElement('header');
      const label = document.createElement('label');
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.dataset.sectionId = text(section.section_id); checkbox.checked = saved.selected.has(section.section_id);
      const title = document.createElement('strong'); title.textContent = `${index + 1}. ${text(section.title)}`;
      label.append(checkbox, title);
      const verdict = document.createElement('span'); verdict.className = `status-kicker ${section.verdict === 'supported' ? 'success' : 'warning'}`; verdict.textContent = verdictLabel(section.verdict);
      header.append(label, verdict);
      const details = document.createElement('details');
      const summary = document.createElement('summary'); summary.textContent = '查看正文与审核意见';
      const body = document.createElement('div'); body.className = 'academic-section-body'; renderMarkdownInto(body, text(section.content));
      const review = document.createElement('div'); review.className = 'academic-section-review';
      const issues = document.createElement('p'); issues.textContent = asArray(section.issues).length ? `问题标签：${asArray(section.issues).join('、')}` : '问题标签：无';
      const rationale = document.createElement('p'); rationale.textContent = `审核说明：${text(section.rationale) || '未提供'}`;
      review.append(issues, rationale); details.append(summary, body, review);
      const feedbackLabel = document.createElement('label'); feedbackLabel.textContent = '本节修改意见';
      const textarea = document.createElement('textarea'); textarea.rows = 2; textarea.maxLength = 2000; textarea.dataset.sectionId = text(section.section_id); textarea.placeholder = '仅在需要本节特殊修改时填写'; textarea.value = saved.feedback.get(section.section_id) || '';
      feedbackLabel.append(textarea); card.append(header, details, feedbackLabel); dom.academicEditableSections.append(card);
    });
    dom.academicGlobalFeedback.value = saved.global;
    dom.academicRevisionPanel.hidden = false;
    renderPendingRevision(workspace);
    renderVersionHistory(workspace);
    renderBusyControls();
    persistAcademicSession();
  }

  function renderPendingRevision(workspace) {
    const pending = workspace.pending;
    dom.academicRevisionComparison.replaceChildren();
    dom.academicRevisionPreview.hidden = !pending;
    if (!pending) return;
    dom.academicCandidateStatus.textContent = `候选 v${pending.version} · 基于 v${pending.parent_version} · ${pending.machine_ready ? '机器审核通过' : '仍需人工确认'}`;
    const current = new Map(asArray(workspace.sections).map((section) => [section.section_id, section]));
    const selected = new Set(asArray(pending.section_ids));
    asArray(pending.sections).filter((section) => selected.has(section.section_id)).forEach((section) => {
      const oldSection = current.get(section.section_id) || {};
      const item = document.createElement('section'); item.className = 'academic-editor-section';
      const heading = document.createElement('h4'); heading.textContent = text(section.title) || text(section.section_id);
      const instruction = document.createElement('p'); instruction.textContent = `修改意见：${text(pending.section_feedback && pending.section_feedback[section.section_id]) || text(pending.feedback) || '—'}`;
      const grid = document.createElement('div'); grid.className = 'academic-compare-grid';
      const before = document.createElement('article'); const beforeTitle = document.createElement('strong'); beforeTitle.textContent = `当前稿 · ${text(oldSection.content).length} 字符`; const beforeBody = document.createElement('div'); renderMarkdownInto(beforeBody, text(oldSection.content)); before.append(beforeTitle, beforeBody);
      const after = document.createElement('article'); const afterTitle = document.createElement('strong'); afterTitle.textContent = `候选稿 · ${text(section.content).length} 字符`; const afterBody = document.createElement('div'); renderMarkdownInto(afterBody, text(section.content)); after.append(afterTitle, afterBody);
      grid.append(before, after);
      const review = document.createElement('p'); review.className = 'academic-section-review'; review.textContent = `${verdictLabel(section.verdict)} · ${asArray(section.issues).join('、') || '无问题标签'}`;
      item.append(heading, instruction, grid, review); dom.academicRevisionComparison.append(item);
    });
  }

  function renderVersionHistory(workspace) {
    dom.academicVersionHistory.replaceChildren();
    asArray(workspace.versions).slice().sort((a, b) => b.version - a.version).forEach((version) => {
      const row = document.createElement('p');
      const label = document.createElement('span');
      const decision = { accepted: '已采用', pending: '待决定', discarded: '已放弃' }[version.decision] || text(version.decision);
      label.textContent = `v${version.version} · ${decision}`;
      row.append(label); addArtifactLink(row, '草稿', version.draft_url); addArtifactLink(row, '审计', version.audit_url);
      dom.academicVersionHistory.append(row);
    });
  }

  function renderWorkspaceAsReport(workspace) {
    const markdown = asArray(workspace && workspace.sections).map((section) => `## ${text(section.title)}\n\n${text(section.content)}`).join('\n\n');
    if (markdown) renderReport(markdown);
  }

  function handleAcademicMessage(data) {
    dom.academicWorkflowPanel.hidden = false;
    switch (data.type) {
      case 'academic_progress':
        updateAcademicStage(text(data.stage)); appendActivity('Academic LangGraph', text(data.label) || text(data.stage));
        if (data.stage === 'writing') setRunState('writing', '逐节写作', state.run.task);
        else if (data.stage === 'review') setRunState('reviewing', '引用复核', state.run.task);
        else setRunState('running', text(data.label) || '学术工作流', state.run.task);
        break;
      case 'academic_outline':
        state.academic.sessionId = text(data.session_id); state.academic.busy = false; state.run.active = false;
        renderAcademicOutline(data); updateAcademicStage('approval');
        setRunState('awaiting_outline', '等待目录审批', '确认目录后才会进入付费写作。'); renderBusyControls();
        break;
      case 'academic_review_required':
        state.academic.sessionId = text(data.session_id) || state.academic.sessionId; state.academic.busy = false; state.run.active = false; state.academic.exported = false;
        dom.academicOutlinePanel.hidden = true;
        dom.academicFinalPanel.hidden = true;
        renderReviewMessage(data); renderDraftWorkspace(data.draft_workspace, { preserveInputs: false }); renderWorkspaceAsReport(data.draft_workspace);
        updateAcademicStage('human_review'); setRunState('review_required', '需要人工确认', '草稿已保存；可定向修订，或人工确认后直接导出。'); renderBusyControls();
        break;
      case 'academic_ready':
        state.academic.sessionId = text(data.session_id) || state.academic.sessionId; state.academic.busy = false; state.run.active = false; state.academic.exported = true;
        if (data.draft_workspace) renderDraftWorkspace(data.draft_workspace, { preserveInputs: false });
        renderReport(text(data.markdown)); updateDownloads({ md: data.url }, true);
        dom.academicOutlinePanel.hidden = true;
        dom.academicReviewPanel.hidden = true;
        dom.academicRevisionPanel.hidden = true;
        dom.academicFinalPanel.hidden = false; configureFinalPanel('machine_ready_export', true, data.url);
        updateAcademicStage('complete'); setRunState('ready', '机器审核通过', '报告已完成，仍可继续审阅或导出当前版本。'); renderBusyControls();
        if (state.report.markdown) saveHistoryEntry({ prompt: state.run.task || 'Academic report', report: state.report.markdown, links: state.report.links, reportType: 'academic_langgraph', reportSource: 'academic', tone: 'Formal', queryDomains: '' });
        break;
      case 'academic_revision_preview':
        state.academic.busy = false; state.run.active = false; state.academic.lastAction = 'preview';
        renderDraftWorkspace(data.draft_workspace, { preserveInputs: true }); renderWorkspaceReview(data.draft_workspace); updateAcademicStage('human_review');
        setRunState('review_required', '候选稿待决定', '候选版本已保存，请比较后采用或放弃。'); showToast('候选稿已生成，当前采用稿尚未被覆盖。');
        break;
      case 'academic_draft_updated': {
        state.academic.busy = false; state.run.active = false; state.academic.recovering = false; state.academic.recoveryAttempts = 0;
        if (data.draft_workspace) {
          dom.academicOutlinePanel.hidden = true;
          const versionChanged = state.academic.workspace && state.academic.workspace.current_version !== data.draft_workspace.current_version;
          renderDraftWorkspace(data.draft_workspace, { preserveInputs: !versionChanged }); renderWorkspaceReview(data.draft_workspace); renderWorkspaceAsReport(data.draft_workspace);
          setRunState(data.draft_workspace.machine_ready ? 'ready' : 'review_required', data.draft_workspace.pending ? '候选稿待决定' : data.draft_workspace.machine_ready ? '当前稿已通过' : '当前稿需确认');
        } else {
          if (state.academic.outline) renderAcademicOutline(state.academic.outline);
          showToast('该会话尚无可恢复的草稿。', 'error'); setRunState('awaiting_outline', '等待目录审批');
        }
        break;
      }
      case 'academic_final': handleAcademicFinal(data); break;
      case 'academic_error': handleAcademicError(text(data.code) || 'internal_error'); break;
      default: break;
    }
  }

  function configureFinalPanel(result, machineReady, url) {
    const heading = dom.academicFinalPanel.querySelector('h3');
    const description = dom.academicFinalPanel.querySelector('h3 + p');
    const kicker = dom.academicFinalPanel.querySelector('.status-kicker');
    dom.academicFinalLinks.replaceChildren();
    if (result === 'rejected') {
      heading.textContent = '目录已拒绝，工作流已结束'; description.textContent = '没有进入付费写作，也没有生成报告。'; kicker.textContent = 'REJECTED'; dom.academicContinueRevision.hidden = true; return;
    }
    heading.textContent = machineReady ? '学术报告已通过并导出' : '已按人工确认导出';
    description.textContent = machineReady ? '机器引用审核通过，当前版本已生成最终 Markdown。' : '此导出基于明确人工确认，不代表机器审核结论为 ready。';
    kicker.textContent = machineReady ? 'MACHINE READY' : 'HUMAN CONFIRMED';
    addArtifactLink(dom.academicFinalLinks, '下载最终 Markdown', url);
    dom.academicContinueRevision.hidden = !state.academic.workspace;
  }

  function handleAcademicFinal(data) {
    state.academic.busy = false; state.run.active = false;
    const rejected = data.result === 'rejected';
    if (data.draft_workspace) { renderDraftWorkspace(data.draft_workspace, { preserveInputs: false }); renderWorkspaceReview(data.draft_workspace); }
    if (typeof data.markdown === 'string') renderReport(data.markdown);
    if (data.url) updateDownloads({ md: data.url }, true);
    state.academic.exported = !rejected;
    if (rejected) { state.academic.sessionId = null; sessionStorage.removeItem(ACADEMIC_SESSION_KEY); }
    dom.academicOutlinePanel.hidden = true; dom.academicReviewPanel.hidden = true; dom.academicRevisionPanel.hidden = true; dom.academicFinalPanel.hidden = false;
    configureFinalPanel(text(data.result), Boolean(data.machine_disposition_ready), data.url);
    updateAcademicStage('complete'); setRunState(rejected ? 'rejected' : 'exported', rejected ? '已拒绝' : '已导出', rejected ? '目录被拒绝，未进入写作。' : '最终报告已生成。');
    if (!rejected && state.report.markdown) saveHistoryEntry({ prompt: state.run.task || (state.academic.outline && state.academic.outline.title) || 'Academic report', report: state.report.markdown, links: state.report.links, reportType: 'academic_langgraph', reportSource: 'academic', tone: 'Formal', queryDomains: '' });
    persistAcademicSession(); renderBusyControls();
  }

  function handleAcademicError(code) {
    state.academic.busy = false; state.run.active = false;
    const message = ACADEMIC_ERROR_MESSAGES[code] || `学术工作流错误：${code}`;
    appendActivity('Academic workflow error', `${code} · ${message}`, 'error'); showToast(message, 'error', 5200);
    if (code === 'session_busy' && state.academic.sessionId) {
      state.academic.recovering = true;
      scheduleAcademicRecovery();
      renderBusyControls();
      return;
    }
    if (['stale_version', 'revision_pending'].includes(code) && state.academic.sessionId) {
      cancelReconnectTimer();
      const sessionId = state.academic.sessionId;
      state.academic.recovering = true;
      state.connection.reconnectTimer = window.setTimeout(() => {
        state.connection.reconnectTimer = null;
        if (!state.academic.recovering || state.academic.sessionId !== sessionId || state.run.kind !== 'academic') return;
        sendCommand('academic_draft_state', { session_id: sessionId });
      }, 350);
    }
    if (code === 'session_not_found' || code === 'invalid_session') { sessionStorage.removeItem(ACADEMIC_SESSION_KEY); state.academic.sessionId = null; }
    setRunState('error', `Academic · ${code}`, message); renderBusyControls();
  }

  function decideOutline(decision) {
    if (!state.academic.sessionId || state.academic.busy || state.run.status !== 'awaiting_outline') return;
    const approved = decision === 'approve';
    const confirmation = approved ? '批准后将开始逐节写作与引用审核，并产生多次 LLM 调用费用。确认继续？' : '拒绝目录会安全结束本次工作流，不会进入付费写作。确认拒绝？';
    if (!window.confirm(confirmation)) return;
    state.academic.busy = true; state.run.active = true; state.academic.lastAction = `decision:${decision}`; renderBusyControls();
    if (approved) { updateAcademicStage('writing'); setRunState('writing', '逐节写作', '目录已批准，正在生成章节并审核引用。'); }
    else setRunState('running', '结束工作流', '正在记录拒绝决定。');
    sendCommand('academic_decision', { session_id: state.academic.sessionId, decision });
  }

  function selectedRevisionPayload() {
    const selected = []; const sectionFeedback = {};
    dom.academicEditableSections.querySelectorAll('input[type="checkbox"][data-section-id]').forEach((checkbox) => {
      if (!checkbox.checked) return;
      selected.push(checkbox.dataset.sectionId);
      const textarea = dom.academicEditableSections.querySelector(`textarea[data-section-id="${CSS.escape(checkbox.dataset.sectionId)}"]`);
      const value = textarea ? textarea.value.trim() : '';
      if (value) sectionFeedback[checkbox.dataset.sectionId] = value;
    });
    return { selected, feedback: dom.academicGlobalFeedback.value.trim(), sectionFeedback };
  }

  function createRequestId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    return `revision-${Date.now()}-${Math.random().toString(36).slice(2, 14)}`;
  }

  function submitAcademicRevision() {
    const workspace = state.academic.workspace;
    if (!workspace || !state.academic.sessionId || state.academic.busy || workspace.pending) return;
    const { selected, feedback, sectionFeedback } = selectedRevisionPayload();
    const notice = dom.academicRevisionNotice;
    if (!selected.length || selected.length > 10) { notice.textContent = '请选择 1–10 个需要修改的章节。'; return; }
    if (feedback.length > 4000 || Object.values(sectionFeedback).some((value) => value.length > 2000)
      || feedback.length + Object.values(sectionFeedback).reduce((sum, value) => sum + value.length, 0) > 12000) {
      notice.textContent = '修改意见超过长度限制：总体 4000、单节 2000、合计 12000 字符。'; return;
    }
    if (selected.some((id) => !feedback && !sectionFeedback[id])) { notice.textContent = '每个选中章节都需要总体意见或本节意见。'; return; }
    if (!window.confirm(`将修订 ${selected.length} 个章节，并对完整草稿重新执行引用审核。这会产生 LLM 调用费用，确认继续？`)) return;
    notice.textContent = '修订请求已发送；当前采用稿不会被自动覆盖。';
    state.academic.busy = true; state.run.active = true; state.academic.lastAction = 'revise'; renderBusyControls();
    setRunState('writing', '定向修订', `正在修订 ${selected.length} 个章节并复审。`);
    sendCommand('academic_revise', {
      session_id: state.academic.sessionId, base_version: workspace.current_version, request_id: createRequestId(),
      section_ids: selected, feedback, section_feedback: sectionFeedback,
    });
  }

  function decideRevision(decision) {
    const workspace = state.academic.workspace; const pending = workspace && workspace.pending;
    if (!pending || state.academic.busy || !state.academic.sessionId) return;
    const action = decision === 'accept' ? '采用候选稿' : '放弃候选稿并保留当前版本';
    if (!window.confirm(`${action}？此操作不调用 LLM。`)) return;
    state.academic.busy = true; state.run.active = true; state.academic.lastAction = `revision:${decision}`; renderBusyControls();
    sendCommand('academic_revision_decision', { session_id: state.academic.sessionId, base_version: workspace.current_version, version: pending.version, decision });
  }

  function exportAcademic() {
    const workspace = state.academic.workspace;
    if (!workspace || workspace.pending || state.academic.busy || !state.academic.sessionId) return;
    const warning = workspace.machine_ready ? '导出当前机器审核通过的版本？此操作不调用 LLM。' : '当前机器结论仍需人工复核。继续将以“明确人工确认”导出，不会声称机器审核通过，也不会调用 LLM。确认继续？';
    if (!window.confirm(warning)) return;
    state.academic.busy = true; state.run.active = true; state.academic.lastAction = 'export'; renderBusyControls();
    setRunState('running', '导出报告', '正在生成当前版本的最终 Markdown。');
    sendCommand('academic_export', { session_id: state.academic.sessionId, version: workspace.current_version });
  }

  function continueAcademicRevision() {
    if (!state.academic.workspace) return;
    state.academic.exported = false; dom.academicFinalPanel.hidden = true; dom.academicRevisionPanel.hidden = false;
    dom.academicReviewPanel.hidden = state.academic.workspace.machine_ready;
    updateAcademicStage('human_review');
    setRunState(state.academic.workspace.machine_ready ? 'ready' : 'review_required', '继续审阅', '当前导出文件保持不变；可基于当前版本继续定向修订。');
    dom.academicRevisionPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function selectAllAcademicSections(selected) {
    dom.academicEditableSections.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => { checkbox.checked = selected; });
  }

  function addChatMessage(message, role, { persist = true } = {}) {
    const item = document.createElement('div'); item.className = `chat-message ${role === 'user' ? 'user-message' : 'ai-message'}`;
    const body = document.createElement('div');
    if (role === 'assistant') body.append(...markdownFragment(message).childNodes); else body.textContent = text(message);
    const timestamp = document.createElement('time'); timestamp.className = 'chat-timestamp'; timestamp.dateTime = new Date().toISOString(); timestamp.textContent = formatClock(Date.now());
    item.append(body, timestamp); dom.chatMessages.append(item); dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
    if (persist) state.chat.messages.push({ role: role === 'user' ? 'user' : 'assistant', content: text(message) });
  }

  function addChatLoading() {
    removeChatLoading();
    const item = document.createElement('div'); item.className = 'chat-message ai-message chat-loading'; item.textContent = '正在整理回答…'; dom.chatMessages.append(item);
  }
  function removeChatLoading() { dom.chatMessages.querySelectorAll('.chat-loading').forEach((item) => item.remove()); }

  function sendChatMessage() {
    const message = dom.chatInput.value.trim();
    if (!message || state.chat.waiting) return;
    if (!state.report.markdown) { showToast('请先完成一份报告。', 'error'); return; }
    addChatMessage(message, 'user'); dom.chatInput.value = ''; dom.chatInput.style.height = ''; state.chat.waiting = true; addChatLoading();
    const messages = state.chat.messages.slice(-12).map((item) => ({ role: item.role, content: item.content.slice(0, 12000) }));
    sendCommand('chat', { message, report: state.report.markdown, messages });
  }

  async function copyReport() {
    if (!state.report.markdown) return;
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(state.report.markdown);
      else {
        const area = document.createElement('textarea'); area.value = state.report.markdown; area.style.position = 'fixed'; area.style.opacity = '0'; document.body.append(area); area.select(); document.execCommand('copy'); area.remove();
      }
      showToast('报告已复制到剪贴板。');
    } catch (_) { showToast('复制失败，请手动选择报告内容。', 'error'); }
  }

  function toggleExpanded(card, button) {
    const expanded = !card.classList.contains('expanded-view');
    document.querySelectorAll('.expanded-view').forEach((item) => item.classList.remove('expanded-view'));
    card.classList.toggle('expanded-view', expanded); button.setAttribute('aria-expanded', String(expanded)); button.title = expanded ? '收起' : '展开';
  }

  function openDrawer(drawer, trigger) {
    closeDrawers({ restoreFocus: false }); lastFocusedDrawerTrigger = trigger;
    drawer.inert = false; drawer.classList.add('open'); drawer.setAttribute('aria-hidden', 'false'); trigger.setAttribute('aria-expanded', 'true'); dom.drawerScrim.hidden = false;
    const focusTarget = drawer.querySelector('button, input, select, a[href]'); if (focusTarget) focusTarget.focus();
  }

  function closeDrawers({ restoreFocus = true } = {}) {
    [byId('websocketPanel'), byId('historyPanel')].forEach((drawer) => { drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true'); drawer.inert = true; });
    [byId('websocketPanelOpenBtn'), byId('historyPanelOpenBtn')].forEach((button) => button.setAttribute('aria-expanded', 'false'));
    dom.drawerScrim.hidden = true;
    if (restoreFocus && lastFocusedDrawerTrigger) lastFocusedDrawerTrigger.focus();
    lastFocusedDrawerTrigger = null;
  }

  function handleGlobalKeydown(event) {
    if (event.key === 'Escape') {
      closeDrawers();
      document.querySelectorAll('.expanded-view').forEach((item) => item.classList.remove('expanded-view'));
      return;
    }
    if (event.key !== 'Tab') return;
    const drawer = document.querySelector('.drawer.open');
    if (!drawer) return;
    const focusable = [...drawer.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
      .filter((item) => !item.hidden && item.getClientRects().length > 0);
    if (!focusable.length) { event.preventDefault(); drawer.focus(); return; }
    const first = focusable[0]; const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function historyId() {
    return window.crypto && typeof window.crypto.randomUUID === 'function' ? window.crypto.randomUUID() : `history-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function normalizeHistoryEntry(entry) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
    const prompt = text(entry.prompt || entry.task).slice(0, 12000); const report = text(entry.report).slice(0, 1_500_000);
    if (!prompt || !report) return null;
    const links = {};
    Object.keys(DOWNLOAD_IDS).forEach((format) => { const safe = normalizeOutputUrl(entry.links && entry.links[format]); if (safe) links[format] = safe; });
    return {
      id: text(entry.id) || historyId(), prompt, report, links,
      reportType: text(entry.reportType || entry.report_type), reportSource: text(entry.reportSource || entry.report_source),
      tone: text(entry.tone), queryDomains: text(entry.queryDomains || entry.query_domains),
      timestamp: Number.isFinite(Number(entry.timestamp)) ? Number(entry.timestamp) : Date.now(),
    };
  }

  function loadHistory() {
    let raw = null;
    try { raw = localStorage.getItem(HISTORY_KEY); } catch (_) { raw = null; }
    if (!raw) {
      const cookie = document.cookie.split(';').map((item) => item.trim()).find((item) => item.startsWith(`${HISTORY_KEY}=`));
      if (cookie) { try { raw = decodeURIComponent(cookie.slice(HISTORY_KEY.length + 1)); } catch (_) { raw = null; } }
    }
    try { state.history = asArray(JSON.parse(raw || '[]')).map(normalizeHistoryEntry).filter(Boolean).slice(0, MAX_HISTORY_ENTRIES); } catch (_) { state.history = []; }
    saveHistory(); renderHistory();
  }

  function saveHistory() {
    state.history = state.history.slice(0, MAX_HISTORY_ENTRIES);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history)); } catch (_) { showToast('浏览器存储空间不足，历史记录仅保留在当前页面。', 'error'); }
  }

  function saveHistoryEntry(entry) {
    const normalized = normalizeHistoryEntry({ ...entry, timestamp: Date.now(), id: historyId() });
    if (!normalized || state.history.some((item) => item.prompt === normalized.prompt && item.report === normalized.report)) return;
    state.history.unshift(normalized); saveHistory(); renderHistory();
  }

  function renderHistory() {
    const query = byId('historySearch').value.trim().toLowerCase(); const direction = byId('historySortOrder').value === 'oldest' ? 1 : -1;
    const entries = state.history.filter((entry) => !query || entry.prompt.toLowerCase().includes(query) || entry.report.toLowerCase().includes(query)).slice().sort((a, b) => direction * (a.timestamp - b.timestamp));
    byId('historyEntries').replaceChildren();
    if (!entries.length) { const empty = document.createElement('p'); empty.textContent = query ? '没有匹配的历史记录。' : '还没有完成的研究。'; byId('historyEntries').append(empty); return; }
    entries.forEach((entry) => {
      const article = document.createElement('article'); article.className = 'history-entry'; article.dataset.historyId = entry.id;
      const title = document.createElement('strong'); title.className = 'history-entry-title'; title.textContent = entry.prompt;
      const meta = document.createElement('p'); meta.className = 'history-entry-meta'; meta.textContent = `${new Date(entry.timestamp).toLocaleString('zh-CN')} · ${entry.reportType || 'report'}`;
      const links = document.createElement('div'); links.className = 'history-entry-links';
      Object.entries(entry.links).forEach(([format, url]) => { const anchor = document.createElement('a'); anchor.href = url; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; anchor.textContent = format.toUpperCase(); links.append(anchor); });
      const actions = document.createElement('div'); actions.className = 'history-entry-actions';
      [['open', '打开'], ['copy', '复制'], ['delete', '删除']].forEach(([action, label]) => { const button = document.createElement('button'); button.type = 'button'; button.dataset.historyAction = action; button.textContent = label; actions.append(button); });
      article.append(title, meta, links, actions); byId('historyEntries').append(article);
    });
  }

  function historyEntryByElement(element) {
    const article = element.closest('[data-history-id]'); return article ? state.history.find((item) => item.id === article.dataset.historyId) : null;
  }

  function loadHistoryEntry(entry) {
    if (!entry) return;
    if (state.run.active || state.academic.busy) {
      showToast('当前任务仍在运行，完成后再打开历史报告。', 'error');
      return;
    }
    if (state.academic.sessionId && !state.academic.exported) {
      showToast('当前学术草稿仍在审批或修订，请先导出或开始新任务。', 'error');
      return;
    }
    closeSocket();
    clearRunView();
    byId('task').value = entry.prompt;
    if ([...byId('report_type').options].some((option) => option.value === entry.reportType)) byId('report_type').value = entry.reportType;
    if ([...byId('report_source').options].some((option) => option.value === entry.reportSource)) byId('report_source').value = entry.reportSource;
    if ([...byId('tone').options].some((option) => option.value === entry.tone)) byId('tone').value = entry.tone;
    byId('queryDomains').value = entry.queryDomains; renderMode(); updateTaskCount();
    state.run.active = false; state.run.kind = 'history'; state.run.task = entry.prompt;
    renderReport(entry.report); updateDownloads(entry.links); setRunState('finished', '历史报告', entry.prompt); dom.chatContainer.hidden = false;
    addChatMessage('已打开历史报告。你可以基于这份报告继续提问。', 'assistant', { persist: false });
    closeDrawers(); dom.reportCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function copyText(value, successMessage) {
    try { await navigator.clipboard.writeText(value); showToast(successMessage); } catch (_) { showToast('复制失败。', 'error'); }
  }

  function exportHistory() {
    const blob = new Blob([JSON.stringify(state.history, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `research-workbench-history-${new Date().toISOString().slice(0, 10)}.json`; anchor.click(); URL.revokeObjectURL(url);
  }

  function importHistory(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.addEventListener('load', () => {
      try {
        const imported = asArray(JSON.parse(text(reader.result))).map(normalizeHistoryEntry).filter(Boolean);
        if (!imported.length) throw new Error('文件中没有有效历史记录');
        const unique = new Map([...imported, ...state.history].map((entry) => [entry.id, entry]));
        state.history = [...unique.values()].slice(0, MAX_HISTORY_ENTRIES); saveHistory(); renderHistory(); showToast(`已导入 ${imported.length} 条记录。`);
      } catch (error) { showToast(`历史导入失败：${error.message}`, 'error'); }
      byId('historyFileInput').value = '';
    });
    reader.readAsText(file, 'utf-8');
  }

  function showStorageStatus() {
    let bytes = 0; try { bytes = new Blob([localStorage.getItem(HISTORY_KEY) || '']).size; } catch (_) { bytes = 0; }
    showToast(`${state.history.length} 条历史记录 · ${(bytes / 1024).toFixed(1)} KB 本地存储`);
  }

  function addMcpPreset(name) {
    const preset = MCP_PRESETS[name]; if (!preset) return;
    const current = validateMcpConfig(); if (!current) return;
    const index = current.findIndex((item) => item.name === preset.name); if (index >= 0) current[index] = preset; else current.push(preset);
    dom.mcpConfig.value = JSON.stringify(current, null, 2); validateMcpConfig();
  }

  function showMcpInfo() {
    const overlay = document.createElement('div'); overlay.className = 'modal-overlay'; overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true');
    const card = document.createElement('section'); card.className = 'modal-card';
    const header = document.createElement('header'); const title = document.createElement('h2'); title.textContent = 'MCP 配置说明';
    const close = document.createElement('button'); close.type = 'button'; close.className = 'icon-button'; close.textContent = '×'; header.append(title, close);
    const copy = document.createElement('p'); copy.textContent = 'MCP 允许普通研究连接外部工具。配置会随本次 start 请求发送到本地服务；请使用环境变量占位符，不要把密钥写进可共享的浏览器历史。';
    const sample = document.createElement('pre'); sample.textContent = JSON.stringify([MCP_PRESETS.filesystem], null, 2);
    card.append(header, copy, sample); overlay.append(card);
    const dismiss = () => overlay.remove(); close.addEventListener('click', dismiss); overlay.addEventListener('click', (event) => { if (event.target === overlay) dismiss(); });
    document.body.append(overlay); close.focus();
  }

  function setupSpeechRecognition() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) { byId('voiceInputBtn').hidden = true; return; }
    speechRecognition = new Recognition(); speechRecognition.lang = 'zh-CN'; speechRecognition.interimResults = true;
    speechRecognition.addEventListener('result', (event) => { dom.chatInput.value = [...event.results].map((result) => result[0].transcript).join(''); });
    speechRecognition.addEventListener('end', () => byId('voiceInputBtn').setAttribute('aria-pressed', 'false'));
  }

  function bindEvents() {
    dom.researchForm.addEventListener('submit', handleResearchSubmit);
    byId('task').addEventListener('input', updateTaskCount);
    byId('report_type').addEventListener('change', renderMode);
    byId('btnShowAuto').addEventListener('click', () => showToast('Auto Agent 会按研究模式自动选择执行路径。'));
    byId('mcpEnabled').addEventListener('change', () => { dom.mcpConfigSection.hidden = !byId('mcpEnabled').checked; if (byId('mcpEnabled').checked) validateMcpConfig(); });
    dom.mcpConfig.addEventListener('input', validateMcpConfig);
    byId('mcpFormatBtn').addEventListener('click', () => { const parsed = validateMcpConfig(); if (parsed) dom.mcpConfig.value = JSON.stringify(parsed, null, 2); });
    byId('mcpInfoBtn').addEventListener('click', showMcpInfo);
    byId('mcpExampleLink').addEventListener('click', (event) => { event.preventDefault(); showMcpInfo(); });
    document.querySelectorAll('.preset-btn').forEach((button) => button.addEventListener('click', () => addMcpPreset(button.dataset.preset)));
    dom.academicApprove.addEventListener('click', () => decideOutline('approve'));
    dom.academicReject.addEventListener('click', () => decideOutline('reject'));
    byId('academicSelectAll').addEventListener('click', () => selectAllAcademicSections(true));
    byId('academicSelectNone').addEventListener('click', () => selectAllAcademicSections(false));
    dom.academicRevise.addEventListener('click', submitAcademicRevision);
    dom.academicExport.addEventListener('click', exportAcademic);
    dom.academicAcceptRevision.addEventListener('click', () => decideRevision('accept'));
    dom.academicDiscardRevision.addEventListener('click', () => decideRevision('discard'));
    dom.academicContinueRevision.addEventListener('click', continueAcademicRevision);
    ['copyToClipboard', 'copyToClipboardTop'].forEach((id) => byId(id).addEventListener('click', copyReport));
    byId('expandOutputBtn').addEventListener('click', () => toggleExpanded(dom.progressCard, byId('expandOutputBtn')));
    byId('expandReportBtn').addEventListener('click', () => toggleExpanded(dom.reportCard, byId('expandReportBtn')));
    byId('expandChatBtn').addEventListener('click', () => toggleExpanded(dom.chatContainer, byId('expandChatBtn')));
    byId('sendChatBtn').addEventListener('click', sendChatMessage);
    dom.chatInput.addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendChatMessage(); } });
    dom.chatInput.addEventListener('input', () => { dom.chatInput.style.height = 'auto'; dom.chatInput.style.height = `${Math.min(130, dom.chatInput.scrollHeight)}px`; });
    byId('voiceInputBtn').addEventListener('click', () => { if (!speechRecognition) return; byId('voiceInputBtn').setAttribute('aria-pressed', 'true'); speechRecognition.start(); });
    byId('websocketPanelOpenBtn').addEventListener('click', () => openDrawer(byId('websocketPanel'), byId('websocketPanelOpenBtn')));
    byId('historyPanelOpenBtn').addEventListener('click', () => { renderHistory(); openDrawer(byId('historyPanel'), byId('historyPanelOpenBtn')); });
    byId('websocketPanelToggle').addEventListener('click', () => closeDrawers());
    byId('historyPanelToggle').addEventListener('click', () => closeDrawers());
    dom.drawerScrim.addEventListener('click', () => closeDrawers());
    document.addEventListener('keydown', handleGlobalKeydown);
    byId('historySearch').addEventListener('input', renderHistory);
    byId('historySearchBtn').addEventListener('click', renderHistory);
    byId('historySortOrder').addEventListener('change', renderHistory);
    byId('historyClearBtn').addEventListener('click', () => { if (!state.history.length || !window.confirm('清空全部本地研究历史？此操作不可撤销。')) return; state.history = []; saveHistory(); renderHistory(); });
    byId('historyExportBtn').addEventListener('click', exportHistory);
    byId('historyImportBtn').addEventListener('click', () => byId('historyFileInput').click());
    byId('historyStorageBtn').addEventListener('click', showStorageStatus);
    byId('historyFileInput').addEventListener('change', (event) => importHistory(event.target.files[0]));
    byId('historyEntries').addEventListener('click', (event) => {
      const action = event.target.closest('[data-history-action]'); if (!action) return;
      const entry = historyEntryByElement(action); if (!entry) return;
      if (action.dataset.historyAction === 'open') loadHistoryEntry(entry);
      if (action.dataset.historyAction === 'copy') copyText(entry.report, '历史报告已复制。');
      if (action.dataset.historyAction === 'delete') { state.history = state.history.filter((item) => item.id !== entry.id); saveHistory(); renderHistory(); }
    });
    window.addEventListener('beforeunload', () => closeSocket());
  }

  function bootstrap() {
    collectDom(); markdownConverter = createMarkdownConverter(); bindEvents(); setupSpeechRecognition(); loadHistory();
    updateTaskCount(); renderMode(); validateMcpConfig(); resetDownloads(); setRunState('idle'); renderConnection(); restoreAcademicSession();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bootstrap, { once: true });
  else bootstrap();
})();
