// 本文件承载“事件溯源”页面逻辑：事件选择、NewsAPI 分析请求与雷达/轨迹图表渲染。
(function () {
    'use strict';

    const els = {
        eventInput: document.getElementById('traceEventInput'),
        analyzeBtn: document.getElementById('traceAnalyzeBtn'),
        analyzeAgainBtn: document.getElementById('traceAnalyzeAgainBtn'),
        languageSelect: document.getElementById('traceLanguage'),
        daysSelect: document.getElementById('traceDays'),
        quota: document.getElementById('traceQuota'),
        hotList: document.getElementById('traceHotList'),
        historyList: document.getElementById('traceHistoryList'),
        result: document.getElementById('traceResult'),
        resultTitle: document.getElementById('traceResultTitle'),
        metrics: document.getElementById('traceMetrics'),
        narrative: document.getElementById('traceNarrative'),
        milestoneList: document.getElementById('milestoneList'),
        articleList: document.getElementById('traceArticleList'),
    };

    // 图表实例缓存，重新分析前统一 dispose
    let charts = { radar: null, trend: null, spread: null, source: null };

    // 最近一次分析结果与图表展示维度（media=按媒体 / country=按国家）
    let lastResult = null;
    let activeRecordId = 0;
    let traceConfigured = true;
    let radarMode = 'media';
    let spreadMode = 'media';

    const TS_COLORS = ['#4f46e5', '#06b6d4', '#10b981', '#f59e0b', '#f87171', '#8b5cf6', '#14b8a6', '#94a3b8', '#f43f5e', '#3b82f6'];

    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function attrEscape(text) {
        return escapeHtml(String(text == null ? '' : text).replace(/[\r\n]+/g, ' '));
    }

    function formatTime(value) {
        if (!value) return '-';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '-';
        return date.toLocaleString('zh-CN', { hour12: false });
    }

    function formatSpanHours(hours) {
        const h = Number(hours || 0);
        if (h < 24) return `${h.toFixed(1)} 小时`;
        const days = h / 24;
        if (days < 7) return `${days.toFixed(1)} 天`;
        return `${(days / 7).toFixed(1)} 周`;
    }

    function disposeCharts() {
        Object.keys(charts).forEach((key) => {
            if (charts[key]) {
                charts[key].dispose();
                charts[key] = null;
            }
        });
    }

    function setAnalyzing(analyzing) {
        els.analyzeBtn.disabled = analyzing || !traceConfigured;
        els.analyzeAgainBtn.disabled = analyzing || !traceConfigured;
        els.analyzeBtn.textContent = analyzing ? '分析中...' : '开始溯源';
        els.analyzeAgainBtn.textContent = analyzing ? '分析中...' : '重新分析';
    }

    // ---------- 初始化 ----------
    async function init() {
        const params = new URLSearchParams(window.location.search);
        if (params.get('event')) els.eventInput.value = params.get('event');
        const lang = params.get('language');
        if (lang && ['zh', 'en', 'all'].includes(lang)) els.languageSelect.value = lang;
        const days = Number(params.get('days') || 0);
        if (days === 7 || days === 14 || days === 30) els.daysSelect.value = String(days);
        const newsId = Number(params.get('news_id') || 0);
        if (newsId > 0) els.eventInput.dataset.newsId = String(newsId);

        await Promise.all([loadStatus(), loadHotNews(), loadHistory()]);

        els.analyzeBtn.onclick = () => analyze();
        els.analyzeAgainBtn.onclick = () => analyze();
        els.eventInput.onkeydown = (e) => {
            if (e.key === 'Enter') analyze();
        };

        // 图表维度切换：仅重渲染对应图表，不重新请求
        bindModeToggle('radarModeSeg', (mode) => {
            radarMode = mode;
            renderRadar();
        });
        bindModeToggle('spreadModeSeg', (mode) => {
            spreadMode = mode;
            renderSpread();
        });

        // URL 中带记录 ID 时直接恢复数据库快照，避免刷新再次消耗 NewsAPI 额度。
        const recordId = Number(params.get('record_id') || 0);
        if (recordId > 0) {
            await loadTraceRecord(recordId);
            return;
        }

        // 从首页热点新闻跳转过来时自动分析
        const hasEvent = (els.eventInput.value || '').trim();
        const hasNewsId = newsId > 0;
        if (hasEvent || hasNewsId) {
            analyze();
        }
    }

    // 绑定 segment 切换按钮：点击后高亮当前项并回调
    function bindModeToggle(segId, onModeChange) {
        const seg = document.getElementById(segId);
        if (!seg) return;
        seg.querySelectorAll('button').forEach((btn) => {
            btn.onclick = () => {
                seg.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
                btn.classList.add('active');
                onModeChange(btn.dataset.mode);
            };
        });
    }

    async function loadStatus() {
        try {
            const resp = await fetch('/api/trace/status', { cache: 'no-store' });
            if (!resp.ok) return;
            const data = await resp.json();
            traceConfigured = Boolean(data.configured);
            if (data.configured) {
                els.quota.textContent = `NewsAPI 今日剩余额度：${data.remaining_budget} / ${data.daily_budget} 次`;
                els.quota.classList.add('ok');
            } else {
                els.quota.textContent = '⚠️ NewsAPI 未配置（.env 中缺少 NEWSAPI_API_KEY），溯源功能不可用';
                els.quota.classList.add('warn');
            }
            setAnalyzing(false);
        } catch (e) {
            console.error('加载 NewsAPI 状态失败', e);
        }
    }

    async function loadHotNews() {
        try {
            const resp = await fetch('/api/news/top?limit=15&date=24h', { cache: 'no-store' });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            const items = data.data || [];
            if (!items.length) {
                els.hotList.innerHTML = '<span class="trace-empty">暂无热点新闻，请手动输入事件关键词</span>';
                return;
            }
            els.hotList.innerHTML = '';
            items.forEach((item) => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'trace-hot-chip';
                chip.title = item.title || '';
                chip.textContent = item.title || '(无标题)';
                chip.onclick = () => {
                    els.eventInput.value = item.title || '';
                    // 记录 news_id，便于后端用其关键词/实体构建更精确的查询
                    els.eventInput.dataset.newsId = String(item.id || 0);
                    highlightHotChip(chip);
                    analyze();
                };
                els.hotList.appendChild(chip);
            });
        } catch (e) {
            console.error('加载热点新闻失败', e);
            els.hotList.innerHTML = '<span class="trace-empty">热点加载失败</span>';
        }
    }

    // 加载轻量历史列表，完整结果仅在用户点击时按记录 ID 获取。
    async function loadHistory() {
        if (!els.historyList) return;
        try {
            const resp = await fetch('/api/trace/history?limit=20', { cache: 'no-store' });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            const records = Array.isArray(data.data) ? data.data : [];
            if (!records.length) {
                els.historyList.innerHTML = '<span class="trace-empty">暂无溯源记录</span>';
                return;
            }

            els.historyList.innerHTML = '';
            records.forEach((record) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'trace-history-item';
                button.dataset.recordId = String(record.id || 0);
                button.classList.toggle('active', Number(record.id || 0) === activeRecordId);

                const eventName = document.createElement('span');
                eventName.className = 'trace-history-event';
                eventName.textContent = record.event || '未命名事件';

                const meta = document.createElement('span');
                meta.className = 'trace-history-meta';
                meta.textContent = `${formatTime(record.created_at)} · ${Number(record.article_count || 0)} 篇 · ${Number(record.days || 0)} 天`;

                button.append(eventName, meta);
                button.onclick = () => loadTraceRecord(Number(record.id || 0));
                els.historyList.appendChild(button);
            });
        } catch (e) {
            console.error('加载溯源历史失败', e);
            els.historyList.innerHTML = '<span class="trace-empty">历史记录加载失败</span>';
        }
    }

    // 从后端恢复一条完整溯源快照，不重新运行分析。
    async function loadTraceRecord(recordId) {
        if (!recordId) return;
        setAnalyzing(true);
        showLoading();
        try {
            const resp = await fetch(`/api/trace/history/${recordId}`, { cache: 'no-store' });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                showError(`记录加载失败：${(data && data.detail) || `HTTP ${resp.status}`}`);
                return;
            }
            applyRecordContext(data.record || {});
            renderResult(data);
        } catch (e) {
            showError(`记录加载失败：${e.message || e}`);
        } finally {
            setAnalyzing(false);
        }
    }

    // 将历史记录参数同步到操作区，便于基于旧记录重新分析。
    function applyRecordContext(record) {
        if (!record || typeof record !== 'object') return;
        els.eventInput.value = record.event || '';
        if (record.news_id) els.eventInput.dataset.newsId = String(record.news_id);
        else delete els.eventInput.dataset.newsId;
        if (['zh', 'en', 'all'].includes(record.language)) els.languageSelect.value = record.language;
        if ([7, 14, 30].includes(Number(record.days))) els.daysSelect.value = String(record.days);
    }

    // 让当前记录拥有可刷新、可复制的稳定页面地址。
    function updateRecordUrl(recordId) {
        if (!recordId || !window.history || !window.history.replaceState) return;
        const url = new URL(window.location.href);
        url.search = '';
        url.searchParams.set('record_id', String(recordId));
        window.history.replaceState({ recordId }, '', url.toString());
    }

    function highlightHistoryRecord(recordId) {
        if (!els.historyList) return;
        els.historyList.querySelectorAll('.trace-history-item').forEach((item) => {
            item.classList.toggle('active', Number(item.dataset.recordId || 0) === Number(recordId || 0));
        });
    }

    function highlightHotChip(activeChip) {
        els.hotList.querySelectorAll('.trace-hot-chip').forEach((chip) => {
            chip.classList.toggle('active', chip === activeChip);
        });
    }

    // 手动编辑事件文本后，清除已选热点新闻的 news_id，避免沿用旧关键词
    els.eventInput.addEventListener('input', () => {
        delete els.eventInput.dataset.newsId;
        highlightHotChip(null);
        activeRecordId = 0;
        highlightHistoryRecord(0);
        const url = new URL(window.location.href);
        url.searchParams.delete('record_id');
        window.history.replaceState({}, '', url.toString());
    });

    // ---------- 分析 ----------
    async function analyze() {
        const event = (els.eventInput.value || '').trim();
        const newsIdRaw = els.eventInput.dataset.newsId || '';
        if (!event && !newsIdRaw) {
            els.eventInput.focus();
            els.eventInput.classList.add('input-error');
            setTimeout(() => els.eventInput.classList.remove('input-error'), 1200);
            return;
        }

        setAnalyzing(true);
        showLoading();
        try {
            const resp = await fetch('/api/trace/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    event,
                    news_id: newsIdRaw ? Number(newsIdRaw) : null,
                    language: els.languageSelect.value,
                    days: Number(els.daysSelect.value || 14),
                }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                const message = (data && data.detail) || `HTTP ${resp.status}`;
                showError(`分析失败：${message}`);
                return;
            }
            renderResult(data);
            await loadHistory();
        } catch (e) {
            showError(`请求出错：${e.message || e}`);
        } finally {
            setAnalyzing(false);
        }
    }

    function showLoading() {
        els.result.classList.remove('is-hidden');
        els.resultTitle.textContent = '分析中……';
        els.metrics.innerHTML = '<div class="trace-loading">正在通过 NewsAPI 检索全球报道，请稍候…</div>';
        els.narrative.classList.add('is-hidden');
        els.milestoneList.innerHTML = '';
        els.articleList.innerHTML = '';
        disposeCharts();
    }

    function showError(message) {
        els.result.classList.remove('is-hidden');
        els.resultTitle.textContent = '分析失败';
        els.metrics.innerHTML = `<div class="trace-error">${escapeHtml(message)}</div>`;
        els.narrative.classList.add('is-hidden');
    }

    // ---------- 渲染 ----------
    function renderResult(data) {
        const meta = data.meta || {};
        const overview = data.overview || {};
        const empty = meta.reason === 'empty' || !overview.total;

        lastResult = data;
        const record = data.record && typeof data.record === 'object' ? data.record : {};
        if (Number(record.id || 0) > 0) {
            activeRecordId = Number(record.id);
            applyRecordContext(record);
            updateRecordUrl(activeRecordId);
            highlightHistoryRecord(activeRecordId);
        }
        els.result.classList.remove('is-hidden');
        els.resultTitle.textContent = `「${escapeHtml((data.query_label || '事件').slice(0, 60))}」`;

        if (meta.from_cache) {
            appendQuotaNote();
        }

        if (empty) {
            els.metrics.innerHTML = `
                <div class="trace-empty">
                    未检索到相关报道（查询词：${escapeHtml(data.query || '')}）。
                    建议：缩短日期窗口、切换媒体语言，或改用更具体的关键词后重试。
                </div>`;
            els.narrative.classList.add('is-hidden');
            els.milestoneList.innerHTML = '';
            els.articleList.innerHTML = '';
            disposeCharts();
            return;
        }

        renderMetrics(overview);
        renderNarrative(data.narrative || []);
        renderRadar();
        renderTrend(data.timeline || {});
        renderSpread();
        renderSource(data.source_dist || []);
        renderMilestones(data.milestones || []);
        renderArticles(data.articles || []);
    }

    function appendQuotaNote() {
        // 判空后由 loadStatus 的额度提示负责展示，这里保持静默
    }

    function renderMetrics(overview) {
        const first = overview.first_article || {};
        els.metrics.innerHTML = `
            <div class="metric-card">
                <div class="metric-value">${Number(overview.total || 0)}</div>
                <div class="metric-label">相关报道</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">${Number(overview.countries_count || 0)}</div>
                <div class="metric-label">覆盖媒体区</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">${Number(overview.sources_count || 0)}</div>
                <div class="metric-label">报道来源</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">${formatSpanHours(overview.span_hours)}</div>
                <div class="metric-label">传播周期</div>
            </div>
            <div class="metric-card">
                <div class="metric-value metric-value-sm">${formatTime(overview.first_at)}</div>
                <div class="metric-label">首发时间（UTC）</div>
            </div>
        `;
    }

    function renderNarrative(lines) {
        if (!lines || !lines.length) {
            els.narrative.classList.add('is-hidden');
            return;
        }
        els.narrative.classList.remove('is-hidden');
        els.narrative.innerHTML = lines.map((line) => `<p>${escapeHtml(line)}</p>`).join('');
    }

    function renderRadar() {
        const el = document.getElementById('radarChart');
        if (!el || !window.echarts || !lastResult) return;
        const data = radarMode === 'media' ? (lastResult.radar_media || {}) : (lastResult.radar || {});
        const values = data.values || [];
        const indicatorsRaw = data.indicators || [];
        if (!indicatorsRaw.length) return;
        if (charts.radar) charts.radar.dispose();
        charts.radar = echarts.init(el);

        // 轴标签截断，完整名称在 tooltip 中展示
        const indicators = indicatorsRaw.map((ind) => ({
            name: ind.name,
            short: String(ind.name || '').length > 10 ? String(ind.name).slice(0, 10) + '…' : String(ind.name || ''),
            max: Number(ind.max) || 1,
        }));
        const max = Math.max(1, ...values.map((v) => Number(v.value || 0)));
        const seriesName = radarMode === 'media' ? '媒体报道量' : '报道量';

        charts.radar.setOption({
            tooltip: {
                trigger: 'item',
                formatter: (params) => {
                    const vals = Array.isArray(params.value) ? params.value : [];
                    return indicators
                        .map((ind, i) => `${escapeHtml(ind.name)}：${vals[i] != null ? vals[i] : 0}`)
                        .join('<br/>');
                },
            },
            radar: {
                indicator: indicators.map((ind) => ({ name: ind.short, max })),
                radius: '62%',
                splitArea: { areaStyle: { color: ['rgba(79, 70, 229, 0.02)', 'rgba(79, 70, 229, 0.06)'] } },
                axisName: { color: 'var(--text-secondary, #4b5563)', fontSize: 10 },
            },
            series: [{
                type: 'radar',
                data: [{
                    value: values.map((v) => Number(v.value || 0)),
                    name: seriesName,
                    symbolSize: 6,
                    lineStyle: { color: '#4f46e5', width: 2 },
                    itemStyle: { color: '#4f46e5' },
                    areaStyle: { color: 'rgba(79, 70, 229, 0.22)' },
                }],
            }],
        });
    }

    function renderTrend(data) {
        const el = document.getElementById('trendChart');
        if (!el || !window.echarts) return;
        if (charts.trend) charts.trend.dispose();
        charts.trend = echarts.init(el);
        charts.trend.setOption({
            tooltip: { trigger: 'axis' },
            legend: { bottom: 0, data: ['每日报道量', '累计报道量'] },
            grid: { left: 40, right: 48, top: 24, bottom: 52 },
            xAxis: { type: 'category', data: data.dates || [] },
            yAxis: [
                { type: 'value', name: '每日报道量' },
                { type: 'value', name: '累计', min: 0 },
            ],
            series: [
                {
                    name: '每日报道量',
                    type: 'bar',
                    data: data.counts || [],
                    itemStyle: { color: '#06b6d4', borderRadius: [5, 5, 0, 0] },
                },
                {
                    name: '累计报道量',
                    type: 'line',
                    yAxisIndex: 1,
                    smooth: true,
                    data: data.cumulative || [],
                    symbol: 'circle',
                    symbolSize: 5,
                    lineStyle: { color: '#4f46e5', width: 3 },
                    itemStyle: { color: '#4f46e5' },
                    areaStyle: { opacity: 0.08, color: '#4f46e5' },
                },
            ],
        });
    }

    function renderSpread() {
        const el = document.getElementById('spreadChart');
        if (!el || !window.echarts || !lastResult) return;
        const isMedia = spreadMode === 'media';
        const data = isMedia ? (lastResult.spread_media || {}) : (lastResult.spread || {});
        const rows = isMedia ? (data.sources || []) : (data.countries || []);
        const rawSeries = data.series || [];
        if (!rows.length || !rawSeries.length) return;
        if (charts.spread) charts.spread.dispose();
        charts.spread = echarts.init(el);

        // 每个媒体/国家的首发报道用涟漪效果高亮，其余为普通散点
        const series = [];
        rawSeries.forEach((s, index) => {
            const color = TS_COLORS[index % TS_COLORS.length];
            const pts = (s.data || []).slice();
            if (!pts.length) return;
            const sizeOf = (val) => {
                const count = Number((val && val[2]) != null ? val[2] : 1);
                return isMedia ? 12 : Math.min(30, 9 + count * 3);
            };
            series.push({
                name: s.name,
                type: 'effectScatter',
                data: [pts[0]],
                symbolSize: sizeOf,
                itemStyle: { color, shadowBlur: 8, shadowColor: color },
                rippleEffect: { brushType: 'stroke', scale: 2.6 },
                emphasis: { focus: 'series' },
                zlevel: 2,
            });
            if (pts.length > 1) {
                series.push({
                    name: s.name,
                    type: 'scatter',
                    data: pts.slice(1),
                    symbolSize: (val) => Math.max(6, sizeOf(val) - 3),
                    itemStyle: { color, opacity: 0.72 },
                    emphasis: { focus: 'series' },
                });
            }
        });

        charts.spread.setOption({
            tooltip: {
                trigger: 'item',
                formatter: (params) => {
                    if (!params.value) return '';
                    const v = params.value;
                    const time = new Date(v[0]).toLocaleString('zh-CN', { hour12: false });
                    const country = v[3] ? ` · ${escapeHtml(v[3])}` : '';
                    const countInfo = isMedia ? '' : `<br/>报道量：${v[2]}`;
                    const title = v[4] ? `<br/><span style="color:var(--text-light, #99a1b2)">${escapeHtml(v[4])}</span>` : '';
                    return `<b>${escapeHtml(params.name)}</b>${country}<br/>${time}${countInfo}${title}`;
                },
            },
            legend: { bottom: 0, type: 'scroll', data: rows.slice(0, 12) },
            grid: { left: 150, right: 32, top: 24, bottom: 56 },
            xAxis: { type: 'time', name: '时间', nameLocation: 'middle', nameGap: 32 },
            yAxis: {
                type: 'category',
                data: rows,
                name: isMedia ? '媒体' : '国家 / 媒体区',
                axisLabel: { width: 130, overflow: 'truncate' },
            },
            series,
        });
    }

    function renderSource(items) {
        const el = document.getElementById('sourceChart');
        if (!el || !window.echarts) return;
        if (charts.source) charts.source.dispose();
        charts.source = echarts.init(el);
        const names = (items || []).map((item) => item.name);
        const values = (items || []).map((item) => Number(item.value || 0));
        charts.source.setOption({
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            grid: { left: 12, right: 30, top: 12, bottom: 12, containLabel: true },
            xAxis: { type: 'value' },
            yAxis: { type: 'category', data: names.slice().reverse() },
            series: [{
                type: 'bar',
                data: values.slice().reverse(),
                barMaxWidth: 20,
                itemStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                        { offset: 0, color: 'rgba(79, 70, 229, 0.55)' },
                        { offset: 1, color: '#4f46e5' },
                    ]),
                    borderRadius: [0, 6, 6, 0],
                },
                label: { show: true, position: 'right', color: 'var(--text-secondary, #4b5563)' },
            }],
        });
    }

    function renderMilestones(items) {
        if (!items || !items.length) {
            els.milestoneList.innerHTML = '<div class="trace-empty">暂无里程碑数据</div>';
            return;
        }
        els.milestoneList.innerHTML = items.map((item) => `
            <div class="milestone-item milestone-${escapeHtml(item.kind || '')}">
                <div class="milestone-head">
                    <span class="milestone-label">${escapeHtml(item.label || '')}</span>
                    <span class="milestone-time">${formatTime(item.time)}</span>
                </div>
                <div class="milestone-text">${escapeHtml(item.text || '')}</div>
                ${item.title && item.url ? `<a class="milestone-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a>` : ''}
            </div>
        `).join('');
    }

    function renderArticles(items) {
        if (!items || !items.length) {
            els.articleList.innerHTML = '<div class="trace-empty">暂无报道</div>';
            return;
        }
        els.articleList.innerHTML = items.map((item) => `
            <div class="article-item">
                <div class="article-main">
                    <a class="article-title" href="${escapeHtml(item.url || '#')}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || '(无标题)')}</a>
                    ${item.description ? `<div class="article-desc">${escapeHtml(item.description)}</div>` : ''}
                    <div class="article-meta">
                        <span class="article-source">${escapeHtml(item.source_name || '未知来源')}</span>
                        <span class="article-country">${escapeHtml(item.country || '其他')}</span>
                        <span class="article-time">${formatTime(item.published_at)}</span>
                    </div>
                </div>
            </div>
        `).join('');
    }

    // 浏览器窗口尺寸变化时自适应图表
    window.addEventListener('resize', () => {
        Object.keys(charts).forEach((key) => {
            if (charts[key]) charts[key].resize();
        });
    });

    init();
})();
