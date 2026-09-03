// 本文件承载首页新闻流、筛选器与详情弹窗逻辑。
// 全局错误提示：让首页加载失败时有可见反馈。
        window.onerror = function(msg, url, line, col, error) {
            console.error('Global Error:', msg, error);
            const loading = document.getElementById('loadingState');
            if (loading) {
                loading.classList.remove('hidden');
                loading.innerHTML += `<br><span class="inline-error">系统错误: ${msg}</span>`;
            }
            return false;
        };

        const pageDataEl = document.getElementById('page-data');
        const pageData = pageDataEl ? JSON.parse(pageDataEl.textContent || '{}') : {};
        const indexDefaults = (pageData.uiDefaults && pageData.uiDefaults.INDEX) || {};
        const DEFAULT_INDEX_TIME_RANGE = indexDefaults.TIME_RANGE || 'today';
        const DEFAULT_INDEX_SORT_BY = indexDefaults.SORT_BY || 'heat';
        const todayValue = new Date().toISOString().split('T')[0];

        // 页面状态集中管理，避免筛选、分页和弹窗状态互相污染。
        const state = {
            list: [],
            page: 1,
            pageSize: 20,
            searchPageSize: 20,
            loading: false,
            hasMore: true,
            isGenerating: false, // 手动摘要生成的全局锁，避免并发请求。
            filter: {
                q: '',
                source: '',
                category: 'all',
                region: '',
                sortBy: DEFAULT_INDEX_SORT_BY,
                dateOption: DEFAULT_INDEX_TIME_RANGE,
                dateTouched: false,
                startDate: DEFAULT_INDEX_TIME_RANGE === 'today' ? todayValue : '',
                endDate: DEFAULT_INDEX_TIME_RANGE === 'today' ? todayValue : ''
            },
            detail: {
                loading: false
            }
        };

        // 基础筛选选项，分类与来源由接口动态加载。
        const TIME_OPTIONS = [
            { label: '今日', value: 'today' },
            { label: '24小时', value: '24h' },
            { label: '3天内', value: '3d' },
            { label: '7天内', value: '7d' },
            { label: '本周', value: 'week' },
            { label: '本月', value: 'month' },
            { label: '今年', value: 'year' },
            { label: '所有时间', value: 'all' },
            { label: '其他', value: 'other' }
        ];

        const els = {
            mobileMenu: document.getElementById('mobileMenu'),
            mobileMoreBtn: document.getElementById('mobileMoreBtn'),
            closeMobileMenu: document.getElementById('closeMobileMenu'),
            applyMobileFilter: document.getElementById('applyMobileFilter'),

            mobileTopSearchInput: document.getElementById('mobileTopSearchInput'),
            mobileTopSearchBtn: document.getElementById('mobileTopSearchBtn'),
            desktopSearch: document.getElementById('desktopSearchInput'),
            desktopSearchBtn: document.getElementById('desktopSearchBtn'),
            desktopResetBtn: document.getElementById('desktopResetBtn'),

            mobileDateRange: document.getElementById('mobileDateRange'),
            mobileStartDate: document.getElementById('mobileStartDate'),
            mobileEndDate: document.getElementById('mobileEndDate'),

            desktopDateRange: document.getElementById('desktopDateRange'),
            desktopStartDate: document.getElementById('desktopStartDate'),
            desktopEndDate: document.getElementById('desktopEndDate'),

            mobileTimeContainer: document.getElementById('mobileTimeOptions'),
            desktopTimeSelect: document.getElementById('desktopTimeSelect'),

            categoryTabs: document.getElementById('categoryTabs'),

            mobileRegionContainer: document.getElementById('mobileRegionContainer'),
            desktopRegionSelect: 'desktopRegionSelect',

            mobileSourceContainer: document.getElementById('mobileSourceContainer'),
            desktopSourceSelect: 'desktopSourceSelect',

            newsList: document.getElementById('newsList'),
            loading: document.getElementById('loadingState'),

            sortBtns: {
                mobileHeat: document.getElementById('mobileSortHeat'),
                mobileDate: document.getElementById('mobileSortDate'),
                desktopHeat: document.getElementById('desktopSortHeat'),
                desktopDate: document.getElementById('desktopSortDate')
            }
        };

        const detailEls = {
            modal: document.getElementById('newsDetailModal'),
            closeBtn: document.getElementById('detailCloseBtn'),
            title: document.getElementById('detailTitle'),
            body: document.getElementById('detailBody')
        };

        const termEls = {
            modal: document.getElementById('termAnalysisModal'),
            closeBtn: document.getElementById('termAnalysisCloseBtn'),
            title: document.getElementById('termAnalysisTitle'),
            body: document.getElementById('termAnalysisBody')
        };

        let activeTermAnalysis = { term: "", range: "year" };
        let termAnalysisAbortController = null;
        let termAnalysisRequestId = 0;
        const termAnalysisCache = new Map();
        const TERM_ANALYSIS_CACHE_LIMIT = 12;
        let chartIndexTermTrend = null;
        let chartIndexTermSentiment = null;
        let chartIndexTermCooccurrence = null;
        const TS_CHART_COLORS = ['#10b981', '#60a5fa', '#f59e0b', '#f87171', '#8b5cf6', '#14b8a6', '#94a3b8'];
        const TS_HEAT_COLOR = '#f59e0b';
        const TS_COUNT_COLOR = '#60a5fa';
        const TERM_TONE_COLORS = {
            entity: { main: '#2563eb', soft: 'rgba(37, 99, 235, 0.13)', stroke: 'rgba(37, 99, 235, 0.32)' },
            negative: { main: '#ef4444', soft: 'rgba(239, 68, 68, 0.13)', stroke: 'rgba(239, 68, 68, 0.32)' },
            positive: { main: '#10b981', soft: 'rgba(16, 185, 129, 0.13)', stroke: 'rgba(16, 185, 129, 0.32)' }
        };

        function isMobileScreen() {
            return window.innerWidth <= 768;
        }

        function normalizeTermName(value) {
            return String(value || '').trim();
        }

        function getTermTone(word) {
            const name = normalizeTermName(typeof word === 'string' ? word : word?.name);
            const rawType = String(typeof word === 'object' ? (word?.type || word?.category || word?.tag_type || word?.sentiment || '') : '').toLowerCase();
            const positiveHints = ['正面', '积极', '利好', '上涨', '增长', '创新', '高质量发展', '成功', '突破', '优秀', '复苏', '合作'];
            const negativeHints = ['负面', '消极', '利空', '下跌', '受伤', '死亡', '事故', '关税', '火灾', '诈骗', '冲突', '风险', '危机', '制裁', '爆炸', '违法'];

            if (rawType.includes('negative') || rawType.includes('负')) return 'negative';
            if (rawType.includes('positive') || rawType.includes('正')) return 'positive';
            if (negativeHints.some(t => name.includes(t))) return 'negative';
            if (positiveHints.some(t => name.includes(t))) return 'positive';
            return 'entity';
        }

        function buildCooccurrenceSymbol(tone) {
            const palette = TERM_TONE_COLORS[tone] || TERM_TONE_COLORS.entity;
            const svg = `
                <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
                    <circle cx="32" cy="32" r="28" fill="${palette.soft}" stroke="${palette.stroke}" stroke-width="2"/>
                    <circle cx="32" cy="32" r="6" fill="${palette.main}"/>
                </svg>
            `.trim();
            return `image://data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
        }

        function styleCooccurrenceNodes(nodes) {
            return (nodes || []).map(node => {
                const tone = getTermTone(node);
                const palette = TERM_TONE_COLORS[tone] || TERM_TONE_COLORS.entity;
                const rawSize = Number(node.symbolSize || node.value || 32);
                const symbolSize = Math.max(26, Math.min(58, rawSize));
                return {
                    ...node,
                    symbol: buildCooccurrenceSymbol(tone),
                    symbolSize,
                    itemStyle: {
                        ...(node.itemStyle || {}),
                        color: palette.main,
                        borderColor: palette.stroke,
                        shadowBlur: 0
                    },
                    label: {
                        ...(node.label || {}),
                        color: palette.main,
                        fontWeight: 750
                    }
                };
            });
        }

        function disposeIndexTermCharts() {
            [chartIndexTermTrend, chartIndexTermSentiment, chartIndexTermCooccurrence].forEach(chart => {
                if (chart) chart.dispose();
            });
            chartIndexTermTrend = null;
            chartIndexTermSentiment = null;
            chartIndexTermCooccurrence = null;
        }

        class MultiSelect {
            constructor(containerIdOrElement, placeholder, options, selectedValues, onChange) {
                if (typeof containerIdOrElement === 'string') {
                    this.container = document.getElementById(containerIdOrElement);
                } else {
                    this.container = containerIdOrElement;
                }

                this.placeholder = placeholder;
                this.options = options || [];
                this.selected = new Set(selectedValues ? selectedValues.split(',').filter(x=>x && x!=='all') : []);
                this.onChange = onChange;
                this.render();
            }

            render() {
                if (!this.container) return;
                this.container.innerHTML = '';

                const trigger = document.createElement('div');
                trigger.className = 'ms-trigger';
                this.updateTriggerText(trigger);
                trigger.onclick = (e) => {
                    e.stopPropagation();
                    this.toggleDropdown();
                };
                this.container.appendChild(trigger);

                const dropdown = document.createElement('div');
                dropdown.className = 'ms-dropdown';
                this.dropdown = dropdown;

                if (this.options.length > 8) {
                    const searchBox = document.createElement('div');
                    searchBox.className = 'ms-search';
                    const input = document.createElement('input');
                    input.placeholder = '搜索...';
                    input.onclick = (e) => e.stopPropagation();
                    input.onkeyup = (e) => {
                        const term = e.target.value.toLowerCase();
                        dropdown.querySelectorAll('.ms-option').forEach(opt => {
                            const txt = opt.textContent.toLowerCase();
                            opt.style.display = txt.includes(term) ? 'flex' : 'none';
                        });
                    };
                    searchBox.appendChild(input);
                    dropdown.appendChild(searchBox);
                }

                this.options.forEach(opt => {
                    const row = document.createElement('div');
                    row.className = 'ms-option';

                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.checked = this.selected.has(opt.value);

                    row.onclick = (e) => {
                        e.stopPropagation();
                        if (this.selected.has(opt.value)) this.selected.delete(opt.value);
                        else this.selected.add(opt.value);

                        cb.checked = this.selected.has(opt.value);
                        this.updateTriggerText(trigger);
                        this.onChange(Array.from(this.selected).join(','));
                    };

                    cb.onclick = (e) => {
                        e.stopPropagation();
                        if (e.target.checked) this.selected.add(opt.value);
                        else this.selected.delete(opt.value);
                        this.updateTriggerText(trigger);
                        this.onChange(Array.from(this.selected).join(','));
                    };

                    row.appendChild(cb);
                    row.appendChild(document.createTextNode(opt.label));
                    dropdown.appendChild(row);
                });

                this.container.appendChild(dropdown);
            }

            updateTriggerText(el) {
                if (this.selected.size === 0) {
                    el.textContent = this.placeholder;
                } else if (this.selected.size === 1) {
                     const val = Array.from(this.selected)[0];
                     const opt = this.options.find(o => o.value === val);
                     el.textContent = opt ? opt.label : val;
                } else {
                    el.textContent = `${this.placeholder} (${this.selected.size})`;
                }
            }

            toggleDropdown() {
                document.querySelectorAll('.ms-dropdown.show').forEach(d => {
                    if (d !== this.dropdown) d.classList.remove('show');
                });
                this.dropdown.classList.toggle('show');
            }

            updateSelected(newValString) {
                 this.selected = new Set(newValString ? newValString.split(',').filter(x=>x && x!=='all') : []);
                 this.render();
            }
        }

        let msRegion, msSource;
        let msMobileRegion, msMobileSource;

        function escapeHtml(text) {
            return String(text || "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        // 对 HTML 属性里的文本做转义（同时压缩换行，避免 title 换行破坏属性）。
        function attrEscape(text) {
            return escapeHtml(String(text || "").replace(/[\r\n]+/g, " "));
        }

        function sentimentClass(label) {
            const v = String(label || "").trim();
            if (v === "正面") return "sent-pos";
            if (v === "负面") return "sent-neg";
            return "sent-neu";
        }

        async function init() {
            renderTimeOptions();

            await Promise.all([fetchRegions(), fetchCategories(), fetchSources()]);

            updateUIState();
            bindEvents();
            fetchNews(true);

            document.addEventListener('click', (e) => {
                if (!e.target.closest('.ms-container')) {
                    document.querySelectorAll('.ms-dropdown.show').forEach(d => d.classList.remove('show'));
                }
            });
        }

        function bindEvents() {
            els.mobileMoreBtn.onclick = () => els.mobileMenu.classList.add('show');
            els.closeMobileMenu.onclick = () => els.mobileMenu.classList.remove('show');
            els.mobileMenu.onclick = (e) => {
                if(e.target === els.mobileMenu) els.mobileMenu.classList.remove('show');
            };

            const applyKeywordSearch = (value) => {
                state.filter.q = String(value || '').trim();
                if (state.filter.q && !state.filter.dateTouched) {
                    setTimeOption('all', { auto: true, skipFetch: true });
                }
                els.mobileTopSearchInput.value = state.filter.q;
                els.desktopSearch.value = state.filter.q;
            };
            const onSearch = (e) => {
                if (e.key === 'Enter') {
                    applyKeywordSearch(e.target.value);
                    fetchNews(true);
                }
            };
            els.mobileTopSearchInput.onkeyup = onSearch;
            els.desktopSearch.onkeyup = onSearch;

            els.mobileTopSearchBtn.onclick = () => {
                applyKeywordSearch(els.mobileTopSearchInput.value);
                fetchNews(true);
            };
            els.desktopSearchBtn.onclick = () => {
                applyKeywordSearch(els.desktopSearch.value);
                fetchNews(true);
            };
            els.desktopResetBtn.onclick = () => {
                state.filter.q = '';
                state.filter.dateOption = DEFAULT_INDEX_TIME_RANGE;
                state.filter.sortBy = DEFAULT_INDEX_SORT_BY;
                state.filter.startDate = '';
                state.filter.endDate = '';
                state.filter.source = '';
                state.filter.region = '';
                state.filter.dateTouched = false;
                els.desktopSearch.value = '';
                els.mobileTopSearchInput.value = '';
                if (msSource) msSource.updateSelected('');
                if (msRegion) msRegion.updateSelected('');
                setTimeOption(DEFAULT_INDEX_TIME_RANGE, { auto: true, skipFetch: true });
                fetchNews(true);
            };

            const onDateChange = (e, type) => {
                state.filter.dateTouched = true;
                const val = e.target.value;
                if (type === 'start') state.filter.startDate = val;
                else state.filter.endDate = val;

                els.mobileStartDate.value = state.filter.startDate;
                els.desktopStartDate.value = state.filter.startDate;
                els.mobileEndDate.value = state.filter.endDate;
                els.desktopEndDate.value = state.filter.endDate;

                if (!isMobile() && state.filter.startDate && state.filter.endDate) {
                    fetchNews(true);
                }
            };

            els.mobileStartDate.onchange = (e) => onDateChange(e, 'start');
            els.desktopStartDate.onchange = (e) => onDateChange(e, 'start');
            els.mobileEndDate.onchange = (e) => onDateChange(e, 'end');
            els.desktopEndDate.onchange = (e) => onDateChange(e, 'end');

            els.desktopTimeSelect.onchange = (e) => {
                setTimeOption(e.target.value);
            };

            const setSort = (val) => {
                state.filter.sortBy = val;
                updateUIState();
                if (!isMobile()) fetchNews(true);
            };
            els.sortBtns.mobileHeat.onclick = () => setSort('heat');
            els.sortBtns.mobileDate.onclick = () => setSort('date');
            els.sortBtns.desktopHeat.onclick = () => setSort('heat');
            els.sortBtns.desktopDate.onclick = () => setSort('date');

            els.applyMobileFilter.onclick = () => {
                applyKeywordSearch(els.mobileTopSearchInput.value);
                els.mobileMenu.classList.remove('show');
                fetchNews(true);
            };

            window.onscroll = () => {
                if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 200) {
                    fetchNews();
                }
            };
        }

        async function fetchRegions() {
            try {
                const res = await fetch('/api/regions');
                if(!res.ok) throw new Error(res.status);
                const list = await res.json();
                const options = list.map(r => ({label: r, value: r}));

                msRegion = new MultiSelect(els.desktopRegionSelect, '全部地区', options, state.filter.region, (val) => {
                    state.filter.region = val;
                    if(msMobileRegion) msMobileRegion.updateSelected(val);
                    fetchNews(true);
                });

                msMobileRegion = new MultiSelect(els.mobileRegionContainer, '全部地区', options, state.filter.region, (val) => {
                    state.filter.region = val;
                    if(msRegion) msRegion.updateSelected(val);
                });

            } catch (e) {
                console.error("Fetch Regions Error:", e);
                msRegion = new MultiSelect(els.desktopRegionSelect, '加载失败', [], '', ()=>{});
            }
        }

        async function fetchCategories() {
            try {
                const res = await fetch('/api/categories');
                if(!res.ok) throw new Error(res.status);
                const list = await res.json();

                const otherIndex = list.indexOf('其他');
                if (otherIndex > -1) {
                    list.push(list.splice(otherIndex, 1)[0]);
                }

                const allCategories = ['全部', ...list];
                renderCategoryTabs(allCategories);

            } catch (e) {
                console.error("Fetch Categories Error:", e);
                renderCategoryTabs(['全部']);
            }
        }

        function renderCategoryTabs(categories) {
            if (!els.categoryTabs) return;
            els.categoryTabs.innerHTML = '';

            categories.forEach(cat => {
                const btn = document.createElement('div');
                btn.className = 'category-tab-item';
                btn.textContent = cat;

                const isAll = (state.filter.category === 'all' || !state.filter.category);
                const isActive = (cat === '全部' && isAll) || (cat === state.filter.category);

                if (isActive) btn.classList.add('active');

                btn.onclick = () => {
                    const val = (cat === '全部') ? 'all' : cat;
                    state.filter.category = val;

                    Array.from(els.categoryTabs.children).forEach(c => c.classList.remove('active'));
                    btn.classList.add('active');

                    const container = els.categoryTabs;
                    const scrollLeft = btn.offsetLeft - (container.offsetWidth / 2) + (btn.offsetWidth / 2);
                    container.scrollTo({
                        left: scrollLeft,
                        behavior: 'smooth'
                    });

                    fetchNews(true);
                };

                els.categoryTabs.appendChild(btn);
            });
        }

        async function fetchSources() {
            try {
                const res = await fetch('/api/sources');
                if(!res.ok) throw new Error(res.status);
                const list = await res.json();
                const options = list.map(s => ({label: s, value: s}));

                msSource = new MultiSelect(els.desktopSourceSelect, '全部来源', options, state.filter.source, (val) => {
                    state.filter.source = val;
                    if(msMobileSource) msMobileSource.updateSelected(val);
                    fetchNews(true);
                });

                msMobileSource = new MultiSelect(els.mobileSourceContainer, '全部来源', options, state.filter.source, (val) => {
                    state.filter.source = val;
                    if(msSource) msSource.updateSelected(val);
                });

            } catch (e) {
                console.error("Fetch Sources Error:", e);
                msSource = new MultiSelect(els.desktopSourceSelect, '加载失败', [], '', ()=>{});
            }
        }

        function renderTimeOptions() {
             const createBtn = (opt) => {
                const btn = document.createElement('button');
                btn.className = 'btn-filter';
                btn.textContent = opt.label;
                btn.dataset.val = opt.value;
                btn.onclick = () => setTimeOption(opt.value);
                return btn;
            };
            els.mobileTimeContainer.innerHTML = '';
            TIME_OPTIONS.forEach(opt => els.mobileTimeContainer.appendChild(createBtn(opt)));

            els.desktopTimeSelect.innerHTML = '';
             TIME_OPTIONS.forEach(opt => {
                const o = document.createElement('option');
                o.value = opt.value;
                o.textContent = opt.label;
                els.desktopTimeSelect.appendChild(o);
            });
        }

        function formatLocalDate(date) {
            const pad = (n) => String(n).padStart(2, '0');
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
        }

        function getDefaultTermDateRange(range = "year") {
            if (range === "all") return { start_date: "", end_date: "" };
            const end = new Date();
            const start = new Date(end);
            if (range === "year") start.setFullYear(start.getFullYear() - 1);
            else start.setDate(start.getDate() - 30);
            return {
                start_date: formatLocalDate(start),
                end_date: formatLocalDate(end)
            };
        }

        function closeTermAnalysis() {
            if (termAnalysisAbortController) {
                termAnalysisAbortController.abort();
                termAnalysisAbortController = null;
            }
            disposeIndexTermCharts();
            termEls.modal.classList.remove('show');
            termEls.modal.setAttribute('aria-hidden', 'true');
            if (!detailEls.modal.classList.contains('show')) document.body.classList.remove('modal-open');
        }

        async function openTermAnalysis(term) {
            const cleanTerm = String(term || '').trim();
            if (!cleanTerm) return;
            activeTermAnalysis = { term: cleanTerm, range: "year" };
            await loadTermAnalysis(cleanTerm, "year", true);
        }

        async function loadTermAnalysis(term, range = "year", openModal = false) {
            const cleanTerm = String(term || '').trim();
            if (!cleanTerm) return;
            activeTermAnalysis = { term: cleanTerm, range };
            const requestId = ++termAnalysisRequestId;
            termEls.title.textContent = cleanTerm;
            if (termAnalysisAbortController) {
                termAnalysisAbortController.abort();
                termAnalysisAbortController = null;
            }
            renderTermAnalysisShell(true);
            if (openModal || !termEls.modal.classList.contains('show')) {
                termEls.modal.classList.add('show');
                termEls.modal.setAttribute('aria-hidden', 'false');
                document.body.classList.add('modal-open');
            }

            try {
                const params = new URLSearchParams({ term: cleanTerm, range });
                const defaultRange = getDefaultTermDateRange(range);
                if (defaultRange.start_date) params.append('start_date', defaultRange.start_date);
                if (defaultRange.end_date) params.append('end_date', defaultRange.end_date);
                const cacheKey = params.toString();
                const cached = termAnalysisCache.get(cacheKey);
                if (cached) {
                    if (requestId !== termAnalysisRequestId) return;
                    renderTermAnalysis(cached, { fromCache: true });
                    return;
                }

                termAnalysisAbortController = new AbortController();
                const resp = await fetch(`/api/report/term-analysis?${cacheKey}`, {
                    cache: 'no-store',
                    signal: termAnalysisAbortController.signal
                });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                if (requestId !== termAnalysisRequestId) return;
                rememberIndexTermAnalysis(cacheKey, data);
                renderTermAnalysis(data);
            } catch (e) {
                if (e.name === 'AbortError') return;
                if (requestId !== termAnalysisRequestId) return;
                termEls.body.innerHTML = `<div class="detail-empty">加载失败：${escapeHtml(e.message)}</div>`;
            } finally {
                if (requestId === termAnalysisRequestId) termAnalysisAbortController = null;
            }
        }

        function changeIndexTermAnalysisRange(range) {
            if (!activeTermAnalysis.term) return;
            loadTermAnalysis(activeTermAnalysis.term, range, false);
        }

        function rememberIndexTermAnalysis(key, data) {
            if (!key) return;
            if (termAnalysisCache.has(key)) termAnalysisCache.delete(key);
            termAnalysisCache.set(key, data);
            while (termAnalysisCache.size > TERM_ANALYSIS_CACHE_LIMIT) {
                termAnalysisCache.delete(termAnalysisCache.keys().next().value);
            }
        }

        function renderTermAnalysisShell(loading = false) {
            disposeIndexTermCharts();
            termEls.body.innerHTML = `
                <div class="term-range-toolbar">
                    <button type="button" class="${activeTermAnalysis.range === '30d' ? 'active' : ''}" data-action="term-range" data-range="30d">最近30天</button>
                    <button type="button" class="${activeTermAnalysis.range === 'year' ? 'active' : ''}" data-action="term-range" data-range="year">1年内</button>
                    <button type="button" class="${activeTermAnalysis.range === 'all' ? 'active' : ''}" data-action="term-range" data-range="all">所有时间</button>
                </div>
                <div class="term-analysis-status ${loading ? 'loading' : ''}">${loading ? '正在汇总词项数据...' : ''}</div>
                <div id="termMetrics" class="term-metrics">
                    <div><strong>-</strong><span>相关报道</span></div>
                    <div><strong>-</strong><span>总热度</span></div>
                    <div><strong>-</strong><span>情绪均值</span></div>
                </div>
                <div class="term-analysis-grid">
                    <div class="term-chart-card">
                        <div class="term-section-title">热度与报道量</div>
                        <div id="termTrendChart" class="term-chart"></div>
                    </div>
                    <div class="term-chart-card">
                        <div class="term-section-title">情绪变化</div>
                        <div id="termSentimentChart" class="term-chart"></div>
                    </div>
                    <div class="term-chart-card full">
                        <div class="term-section-title">关键词共现关系图谱</div>
                        <div id="termCooccurrenceChart" class="term-chart large"></div>
                    </div>
                </div>
                <div class="term-news-section">
                    <div class="term-section-title">相关报道</div>
                    <div id="termNewsList" class="term-news-list"><div class="detail-empty">等待数据...</div></div>
                </div>
            `;
        }

        function renderTermAnalysis(data, options = {}) {
            const summary = data.summary || {};
            const status = termEls.body.querySelector('.term-analysis-status');
            if (status) {
                status.classList.remove('loading');
                status.textContent = options.fromCache ? '已使用最近一次分析结果' : `分析完成${summary.elapsed_ms ? `（${summary.elapsed_ms}ms）` : ''}`;
            }
            const metrics = document.getElementById('termMetrics');
            if (metrics) {
                metrics.innerHTML = `
                    <div><strong>${Number(summary.related_count || 0)}</strong><span>相关报道</span></div>
                    <div><strong>${Number(summary.total_heat || 0).toFixed(1)}</strong><span>总热度</span></div>
                    <div><strong>${Number(summary.avg_sentiment || 0).toFixed(1)}</strong><span>情绪均值</span></div>
                `;
            }
            const newsList = document.getElementById('termNewsList');
            if (newsList) newsList.innerHTML = renderTermNewsList(data.related_news || []);
            setTimeout(() => {
                renderTermTrendChart(data.trend || {});
                renderTermSentimentChart(data.sentiment_trend || {});
                renderTermCooccurrenceChart(data.cooccurrence || {});
            }, 30);
        }

        function renderTermNewsList(items) {
            if (!items.length) return '<div class="detail-empty">暂无相关报道</div>';
            return items.map(item => {
                const time = item.time ? new Date(item.time).toLocaleString('zh-CN') : '-';
                return `
                    <div class="term-news-item">
                        <button type="button" class="detail-inline-link" data-action="open-news-detail" data-news-id="${Number(item.id || 0)}" data-replace="true">${escapeHtml(item.title || '(无标题)')}</button>
                        <div class="term-news-meta">
                            <span>${escapeHtml(item.source || '未知来源')}</span>
                            <span>${time}</span>
                            <span>热度 ${Number(item.heat || 0).toFixed(1)}</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function renderTermTrendChart(data) {
            const el = document.getElementById('termTrendChart');
            if (!el || !window.echarts) return;
            if (chartIndexTermTrend) chartIndexTermTrend.dispose();
            chartIndexTermTrend = echarts.init(el);
            chartIndexTermTrend.setOption({
                color: [TS_COUNT_COLOR, TS_HEAT_COLOR],
                tooltip: { trigger: 'axis' },
                legend: { bottom: 0 },
                grid: { left: 36, right: 42, top: 24, bottom: 48 },
                xAxis: { type: 'category', data: data.dates || [] },
                yAxis: [{ type: 'value', name: '报道量' }, { type: 'value', name: '热度' }],
                series: [
                    { name: '报道量', type: 'bar', data: data.count || [], itemStyle: { color: TS_COUNT_COLOR, borderRadius: [5, 5, 0, 0] } },
                    { name: '热度', type: 'line', yAxisIndex: 1, smooth: true, data: data.heat || [], itemStyle: { color: TS_HEAT_COLOR }, lineStyle: { width: 3, color: TS_HEAT_COLOR }, areaStyle: { opacity: 0.08, color: TS_HEAT_COLOR } }
                ]
            });
        }

        function renderTermSentimentChart(data) {
            const el = document.getElementById('termSentimentChart');
            if (!el || !window.echarts) return;
            if (chartIndexTermSentiment) chartIndexTermSentiment.dispose();
            chartIndexTermSentiment = echarts.init(el);
            chartIndexTermSentiment.setOption({
                color: TS_CHART_COLORS,
                tooltip: { trigger: 'axis' },
                grid: { left: 36, right: 24, top: 24, bottom: 42 },
                xAxis: { type: 'category', data: data.dates || [] },
                yAxis: { type: 'value', min: 0, max: 100 },
                series: data.series || []
            });
        }

        function renderTermCooccurrenceChart(data) {
            const el = document.getElementById('termCooccurrenceChart');
            if (!el || !window.echarts) return;
            if (chartIndexTermCooccurrence) chartIndexTermCooccurrence.dispose();
            chartIndexTermCooccurrence = echarts.init(el);
            const nodes = styleCooccurrenceNodes(data.nodes || []);
            chartIndexTermCooccurrence.setOption({
                tooltip: { trigger: 'item' },
                series: [{
                    type: 'graph',
                    layout: 'force',
                    roam: true,
                    data: nodes,
                    links: data.links || [],
                    label: { show: !isMobileScreen(), position: 'right', distance: 8 },
                    force: { repulsion: 180, edgeLength: 70 },
                    lineStyle: { color: 'rgba(148, 163, 184, 0.48)', width: 1.2, curveness: 0.25 },
                    emphasis: { focus: 'adjacency', lineStyle: { width: 2.2, color: 'rgba(71, 85, 105, 0.72)' } }
                }]
            });
        }


        function closeNewsDetail() {
            detailEls.modal.classList.remove('show');
            detailEls.modal.classList.remove('stacked');
            detailEls.modal.setAttribute('aria-hidden', 'true');
            if (!termEls.modal.classList.contains('show')) document.body.classList.remove('modal-open');
        }

        function renderDetailTags(items, emptyText) {
            const values = (items || []).filter(x => typeof x === 'string' && x.trim()).slice(0, 16);
            if (!values.length) return `<div class="detail-empty">${emptyText}</div>`;
            return values.map(x => `<button type="button" class="detail-chip detail-chip-btn" data-term="${escapeHtml(x)}">${escapeHtml(x)}</button>`).join('');
        }

        function renderDetailNewsList(items, emptyText) {
            if (!items || !items.length) return `<div class="detail-empty">${emptyText}</div>`;
            return items.map(item => {
                const time = item.time ? new Date(item.time).toLocaleString('zh-CN') : '-';
                const itemId = Number(item.id || 0);
                const titleHtml = itemId > 0
                    ? `<button class="detail-inline-link" type="button" data-action="open-news-detail" data-news-id="${itemId}" data-replace="true">${escapeHtml(item.title || '(无标题)')}</button>`
                    : `<a href="${escapeHtml(item.url || '#')}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || '(无标题)')}</a>`;
                return `
                    <div class="detail-list-item">
                        ${titleHtml}
                        <div class="detail-item-meta">
                            <span>${escapeHtml(item.source || '未知来源')}</span>
                            <span>${time}</span>
                            <span>热度 ${Number(item.heat || 0).toFixed(1)}</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function renderRelatedSources(items) {
            if (!items || !items.length) return '<div class="detail-empty">暂无关联报道</div>';
            return items.map((item, idx) => `
                <div class="detail-source-row">
                    <div class="detail-source-index">${idx + 1}</div>
                    <div class="detail-source-main">
                        <div class="detail-source-name">${escapeHtml(item.name || '未知来源')}</div>
                        <a href="${escapeHtml(item.url || '#')}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || item.url || '查看原文')}</a>
                    </div>
                </div>
            `).join('');
        }

        function safeExternalUrl(url) {
            const raw = typeof url === 'string' ? url.trim() : '';
            if (!raw || raw === '#' || raw.startsWith('#')) return '';
            const candidate = raw.startsWith('www.') ? `https://${raw}` : raw;
            try {
                const parsed = new URL(candidate, window.location.origin);
                return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
            } catch (e) {
                return '';
            }
        }

        function renderDetailSourceLink(source, url) {
            const label = escapeHtml(source || '未知来源');
            return url
                ? `<a class="detail-source-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`
                : `<span>${label}</span>`;
        }

        function renderDetailTitleLink(title, url) {
            const label = escapeHtml(title || '新闻详情');
            return url
                ? `<a class="news-detail-title-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`
                : label;
        }

        function renderRelatedTopics(items) {
            if (!items || !items.length) return '<div class="detail-empty">暂未关联专题</div>';
            return items.map(item => `
                <a class="detail-topic-link" href="/topics/${item.id}">
                    <div class="detail-topic-name">${escapeHtml(item.name || '未命名专题')}</div>
                    <div class="detail-topic-meta">热度 ${Number(item.heat_score || 0).toFixed(1)} · ${item.updated_time ? new Date(item.updated_time).toLocaleString('zh-CN') : '-'}</div>
                    ${item.matched_timeline && item.matched_timeline.content ? `<div class="detail-topic-snippet">${escapeHtml(item.matched_timeline.content)}</div>` : ''}
                </a>
            `).join('');
        }

        // 渲染模型从图片中提取的情绪依据与图文核验结果。
        function renderVisualAnalysis(news) {
            const visual = news && news.visual_analysis;
            if (!visual || typeof visual !== 'object' || Number(visual.image_count || 0) <= 0) return '';
            const cues = Array.isArray(visual.sentiment_cues) ? visual.sentiment_cues.filter(Boolean) : [];
            const ocr = Array.isArray(visual.ocr_text) ? visual.ocr_text.filter(Boolean) : [];
            const consistency = visual.text_image_consistency || '无法判断';
            const model = news.analysis_model || 'Qwen3-VL';
            return `
                <div class="detail-section multimodal-analysis-card">
                    <div class="detail-section-title">
                        <span>多模态视觉分析</span>
                        <span class="multimodal-badge">图文联合 · ${Number(visual.image_count || 0)} 张</span>
                    </div>
                    ${visual.summary ? `<div class="multimodal-summary">${escapeHtml(visual.summary)}</div>` : ''}
                    <div class="multimodal-meta"><span>图文关系：${escapeHtml(consistency)}</span><span>模型：${escapeHtml(model)}</span></div>
                    ${cues.length ? `<div class="multimodal-evidence"><strong>视觉情绪线索</strong>${escapeHtml(cues.join(' · '))}</div>` : ''}
                    ${ocr.length ? `<div class="multimodal-evidence"><strong>图片文字</strong>${escapeHtml(ocr.join(' · '))}</div>` : ''}
                </div>
            `;
        }

        // 渲染音视频处理结果，媒体文件仅临时处理，页面只展示可追溯的链接与转写文本。
        function renderMediaAnalysis(news) {
            const media = news && news.media_analysis;
            const transcript = String((news && news.audio_transcript) || '').trim();
            const videos = Array.isArray(news && news.videos) ? news.videos : [];
            const audios = Array.isArray(news && news.audios) ? news.audios : [];
            if ((!media || typeof media !== 'object') && !transcript && !videos.length && !audios.length) return '';
            const links = [
                ...videos.map((url, index) => `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">视频 ${index + 1}</a>`),
                ...audios.map((url, index) => `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">音频 ${index + 1}</a>`),
            ];
            return `
                <div class="detail-section multimodal-analysis-card">
                    <div class="detail-section-title">
                        <span>音视频分析</span>
                        <span class="multimodal-badge">关键帧 ${Number(media?.frame_count || 0)} · 转写 ${Number(media?.transcript_count || 0)}</span>
                    </div>
                    ${transcript ? `<div class="multimodal-evidence"><strong>语音转写</strong>${escapeHtml(transcript)}</div>` : '<div class="detail-empty">未识别到可转写的语音内容。</div>'}
                    ${links.length ? `<div class="multimodal-meta">原始媒体：${links.join(' · ')}</div>` : ''}
                </div>
            `;
        }

        function renderNewsDetail(data) {
            const news = data.news || {};
            const status = data.content_status || {};
            const time = news.time ? new Date(news.time).toLocaleString('zh-CN') : '-';
            const newsUrl = safeExternalUrl(news.url);
            const sourceHtml = renderDetailSourceLink(news.source, newsUrl);
            const summary = news.summary ? marked.parse(news.summary) : '<div class="detail-empty">暂无摘要，可在新闻卡片中生成摘要。</div>';
            const similarSection = `
                <div class="detail-section" id="similarNewsSection">
                    <div class="detail-section-title">可能相似的报道</div>
                    <div id="similarNewsList" class="detail-list">
                        <div class="detail-empty">查询中……</div>
                    </div>
                </div>
            `;
            const topicSection = data.topics && data.topics.length ? `
                <div class="detail-section">
                    <div class="detail-section-title">相关专题</div>
                    <div class="detail-topic-list">${renderRelatedTopics(data.topics)}</div>
                </div>
            ` : '';
            const sourceSection = data.related_sources && data.related_sources.length ? `
                <div class="detail-section">
                    <div class="detail-section-title">关联报道来源</div>
                    <div class="detail-source-list">${renderRelatedSources(data.related_sources)}</div>
                </div>
            ` : '';

            detailEls.title.innerHTML = renderDetailTitleLink(news.title, newsUrl);
            detailEls.body.innerHTML = `
                <div class="detail-meta-line">
                    ${sourceHtml}
                    <span>${time}</span>
                    <span class="meta-heat-val">热度 ${Number(news.heat || 0).toFixed(1)}</span>
                    <span class="meta-sent-val ${sentimentClass(news.sentiment_label)}">${escapeHtml(news.sentiment_label || '未分析')}${news.sentiment_score !== undefined ? '：' + news.sentiment_score : ''}</span>
                    <button class="btn-link" type="button" data-action="trace-event" data-news-id="${news.id}" data-title="${attrEscape(news.title)}">溯源分析</button>
                </div>
                ${renderNewsImages(news.images, true)}
                ${renderVisualAnalysis(news)}
                ${renderMediaAnalysis(news)}
                <div class="detail-status-grid">
                    <div><strong>${status.has_summary ? '已有摘要' : '暂无摘要'}</strong><span>新闻摘要</span></div>
                    <div><strong>${status.related_source_count || 0}</strong><span>关联报道</span></div>
                </div>
                <div class="detail-section">
                    <div class="detail-section-title">
                        <span>新闻摘要</span>
                        <button class="btn-link" data-action="generate-summary" data-news-id="${news.id}">重新生成</button>
                    </div>
                    <div class="detail-summary markdown-body">${summary}</div>
                </div>
                <div class="detail-two-col">
                    <div class="detail-section">
                        <div class="detail-section-title">关键词</div>
                        <div class="detail-chip-row">${renderDetailTags(news.keywords, '暂无关键词')}</div>
                    </div>
                    <div class="detail-section">
                        <div class="detail-section-title">实体</div>
                        <div class="detail-chip-row">${renderDetailTags(news.entities, '暂无实体')}</div>
                    </div>
                </div>
            ${topicSection}
            ${similarSection}
            ${sourceSection}
        `;
        }

        async function loadSimilarNews(id) {
            const listEl = document.getElementById('similarNewsList');
            if (!listEl) return;
            try {
                const resp = await fetch(`/api/news/${id}/similar`, { method: 'GET', cache: 'no-store' });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                const items = data.data || [];
                if (!items.length) {
                    listEl.innerHTML = '<div class="detail-empty">暂无相似报道</div>';
                    return;
                }
                listEl.innerHTML = renderDetailNewsList(items, '暂无相似报道');
            } catch (e) {
                listEl.innerHTML = `<div class="detail-empty">相似报道加载失败：${escapeHtml(e.message)}</div>`;
            }
        }

        window.openNewsDetail = async (id, options = {}) => {
            if (!id || state.detail.loading) return;
            if (options.replace) closeNewsDetail();
            state.detail.loading = true;
            detailEls.title.textContent = '新闻详情';
            detailEls.body.innerHTML = '<div class="detail-loading">加载中...</div>';
            detailEls.modal.classList.add('show');
            detailEls.modal.classList.toggle('stacked', !!options.replace && termEls.modal.classList.contains('show'));
            detailEls.modal.setAttribute('aria-hidden', 'false');
            document.body.classList.add('modal-open');
            try {
                const resp = await fetch(`/api/news/${id}`, { method: 'GET', cache: 'no-store' });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                renderNewsDetail(data);
                detailEls.body.querySelectorAll('[data-term]').forEach(el => {
                    el.onclick = () => openTermAnalysis(el.dataset.term || el.textContent);
                });
                loadSimilarNews(id);
            } catch (e) {
                detailEls.body.innerHTML = `<div class="detail-empty">加载失败：${escapeHtml(e.message)}</div>`;
            } finally {
                state.detail.loading = false;
            }
        };

        detailEls.closeBtn.onclick = closeNewsDetail;
        detailEls.modal.onclick = (e) => {
            if (e.target === detailEls.modal) closeNewsDetail();
        };
        termEls.closeBtn.onclick = closeTermAnalysis;
        termEls.modal.onclick = (e) => {
            if (e.target === termEls.modal) closeTermAnalysis();
        };
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && detailEls.modal.classList.contains('show')) closeNewsDetail();
            if (e.key === 'Escape' && termEls.modal.classList.contains('show')) closeTermAnalysis();
        });


        function isMobile() {
            return window.innerWidth < 768;
        }

        function getEffectiveDate() {
            if (state.filter.dateOption === 'other') return '';
            if (state.filter.dateOption === 'today') return new Date().toISOString().split('T')[0];
            return state.filter.dateOption;
        }

        function setTimeOption(val, options = {}) {
            if (!options.auto) {
                state.filter.dateTouched = true;
            }
            state.filter.dateOption = val;

            const showRange = (val === 'other');
            els.mobileDateRange.classList.toggle('is-hidden', !showRange);
            els.desktopDateRange.classList.toggle('is-hidden', !showRange);

            if (val === 'today') {
                const t = new Date().toISOString().split('T')[0];
                state.filter.startDate = t;
                state.filter.endDate = t;
            } else if (val !== 'other') {
                state.filter.startDate = '';
                state.filter.endDate = '';
            }

            els.mobileStartDate.value = state.filter.startDate;
            els.desktopStartDate.value = state.filter.startDate;
            els.mobileEndDate.value = state.filter.endDate;
            els.desktopEndDate.value = state.filter.endDate;

            updateUIState();
            if (!options.skipFetch && !isMobile() && val !== 'other') fetchNews(true);
        }


        function updateUIState() {
            const updateBtns = (container) => {
                Array.from(container.children).forEach(btn => {
                    if (btn.tagName === 'BUTTON') {
                        if (btn.dataset.val === state.filter.dateOption) {
                            btn.classList.add('active');
                        } else {
                            btn.classList.remove('active');
                        }
                    }
                });
            };
            updateBtns(els.mobileTimeContainer);

            const isPredefined = TIME_OPTIONS.some(o => o.value === state.filter.dateOption);
            if (isPredefined) {
                 els.desktopTimeSelect.value = state.filter.dateOption;
            }

            const s = state.filter.sortBy;
            const setSortActive = (btn, active) => {
                if (active) btn.classList.add('active');
                else btn.classList.remove('active');
            };
            setSortActive(els.sortBtns.mobileHeat, s === 'heat');
            setSortActive(els.sortBtns.mobileDate, s === 'date');
            setSortActive(els.sortBtns.desktopHeat, s === 'heat');
            setSortActive(els.sortBtns.desktopDate, s === 'date');

            els.mobileStartDate.value = state.filter.startDate;
            els.desktopStartDate.value = state.filter.startDate;
            els.mobileEndDate.value = state.filter.endDate;
            els.desktopEndDate.value = state.filter.endDate;
        }

        // 输入新闻时间字符串；输出首页列表使用的“月/日 时:分”格式。
        function formatNewsCardTime(timeValue) {
            if (!timeValue) return '-';
            const date = new Date(timeValue);
            if (Number.isNaN(date.getTime())) return '-';

            const month = date.getMonth() + 1;
            const day = date.getDate();
            const hour = String(date.getHours()).padStart(2, '0');
            const minute = String(date.getMinutes()).padStart(2, '0');
            return `${month}/${day} ${hour}:${minute}`;
        }

        // 渲染新闻配图缩略图（仅展示图片直链，过滤非法地址）
        function renderNewsImages(images, detail = false) {
            if (!Array.isArray(images) || !images.length) return '';
            // 对已入库的 URL 做 HTML 实体兜底解码，避免 &amp; 导致图片加载失败
            const decode = (u) => u
                .replace(/&amp;/g, '&')
                .replace(/&lt;/g, '<')
                .replace(/&gt;/g, '>')
                .replace(/&quot;/g, '"')
                .replace(/&#39;/g, "'");
            const valid = images
                .map(decode)
                .filter(u => typeof u === 'string' && (u.startsWith('http://') || u.startsWith('https://')))
                .slice(0, detail ? 8 : 1);
            if (!valid.length) return '';
            if (detail) {
                const items = valid.map(u => `<img class="news-image-detail-item" loading="lazy" src="${escapeHtml(u)}" alt="配图" referrerpolicy="no-referrer">`).join('');
                return `<div class="news-image-detail">${items}</div>`;
            }
            return `<div class="news-image-thumb"><img class="news-image-thumb-img" loading="lazy" src="${escapeHtml(valid[0])}" alt="配图" referrerpolicy="no-referrer"></div>`;
        }

        function renderNewsList() {
            els.newsList.innerHTML = '';
            state.list.forEach((item, index) => {
                const card = document.createElement('div');
                card.className = 'news-card';
                card.style.setProperty('--card-index', String(index));

                const timeStr = formatNewsCardTime(item.time);
                const hasRelated = item.sources && item.sources.length > 1;
                const heatValue = Number(item.heat || 0);
                const heatWidth = Math.max(8, Math.min(100, heatValue * 6));
                const sourceCount = Array.isArray(item.sources) ? item.sources.length : 0;
                const sentimentLabel = (item.sentiment_label || '未分析').trim();
                let sentimentClass = 'sent-neu';
                if (sentimentLabel === '正面') sentimentClass = 'sent-pos';
                else if (sentimentLabel === '负面') sentimentClass = 'sent-neg';
                const regionText = item.region && item.region !== '其他' ? item.region : '';

                card.innerHTML = `
                    <div class="card-body">
                        <div class="content">
                            <div class="news-head">
                                <button class="news-title news-title-btn" type="button" data-action="open-news-detail" data-news-id="${item.id}">${item.title}</button>
                            </div>

                            ${renderNewsImages(item.images)}

                            <div class="meta-row news-meta-row">
                                <div class="meta-left">
                                    <span class="news-meta-item meta-category">${item.category || '其他'}</span>
                                    <span class="news-meta-item meta-heat">热度 ${heatValue.toFixed(1)}</span>
                                    <span class="news-meta-item meta-sentiment ${sentimentClass}">
                                        ${sentimentLabel}${item.sentiment_score !== undefined ? '：' + item.sentiment_score : ''}
                                    </span>
                                    ${hasRelated ? `<span class="news-meta-item meta-source">来源 ${sourceCount} 个</span>` : ''}
                                    ${regionText ? `<span class="news-meta-item meta-region">${escapeHtml(regionText)}</span>` : ''}
                                </div>
                                <div class="meta-right">
                                    <span class="news-meta-item meta-time">${timeStr}</span>
                                    <span class="news-meta-item meta-action" role="button" tabindex="0" data-action="open-news-detail" data-news-id="${item.id}">详情</span>
                                    <span class="news-meta-item meta-action meta-trace" role="button" tabindex="0" data-action="trace-event" data-news-id="${item.id}" data-title="${attrEscape(item.title)}">溯源</span>
                                </div>
                            </div>
                            <div class="summary-box" id="summary-${item.id}">
                                ${renderSummaryHtml(item.summary, item.id)}
                            </div>
                        </div>
                    </div>
                `;
                els.newsList.appendChild(card);
            });
        }

        async function fetchNews(reset = false) {
            if (state.loading) return;
            if (reset) {
                state.page = 1;
                state.list = [];
                state.hasMore = true;
                els.newsList.innerHTML = '';
            }
            if (!state.hasMore) return;

            state.loading = true;
            els.loading.textContent = '加载中...';
            els.loading.classList.remove('hidden');

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 15000); // 15s timeout

            try {
                const effectivePageSize = state.filter.q ? state.searchPageSize : state.pageSize;
                const effectiveDate = state.filter.q && !state.filter.dateTouched ? 'all' : getEffectiveDate();
                const params = new URLSearchParams({
                    q: state.filter.q,
                    category: (state.filter.category === 'all') ? '' : state.filter.category,
                    region: state.filter.region,
                    source: (state.filter.source === 'all') ? '' : state.filter.source,
                    sort_by: state.filter.sortBy,
                    page: state.page,
                    page_size: effectivePageSize,
                    date: effectiveDate
                });

                if (state.filter.dateOption === 'other') {
                    if (state.filter.startDate) params.append('start_date', state.filter.startDate);
                    if (state.filter.endDate) params.append('end_date', state.filter.endDate);
                }

                const res = await fetch(`/api/news?${params.toString()}`, { signal: controller.signal });
                clearTimeout(timeoutId);

                if (!res.ok) throw new Error(`HTTP Error ${res.status}`);
                const data = await res.json();

                if (data.data.length < effectivePageSize) {
                    state.hasMore = false;
                    els.loading.textContent = '- 到底了 -';
                } else {
                    els.loading.classList.add('hidden');
                }

                if (reset) state.list = data.data;
                else state.list = state.list.concat(data.data);

                state.page++;
                renderNewsList();
            } catch (err) {
                console.error(err);
                if (err.name === 'AbortError') {
                    els.loading.innerHTML = '请求超时。<br><button class="btn-link" data-action="retry-fetch-news">点击重试</button>';
                } else {
                    els.loading.innerHTML = `加载失败: ${err.message}<br><button class="btn-link" data-action="retry-fetch-news">点击重试</button>`;
                }
            } finally {
                state.loading = false;
            }
        }

        function toggleSummary(id, showFull) {
            const content = document.getElementById(`summary-content-${id}`);
            const btnExpand = document.getElementById(`btn-expand-${id}`);
            const btnCollapse = document.getElementById(`btn-collapse-${id}`);

            if (content && btnExpand && btnCollapse) {
                if (showFull) {
                    content.classList.remove('collapsed');
                    btnExpand.classList.add('is-hidden');
                    btnCollapse.classList.remove('is-hidden');
                } else {
                    content.classList.add('collapsed');
                    btnExpand.classList.remove('is-hidden');
                    btnCollapse.classList.add('is-hidden');

                    // 收起时滚动回新闻卡片顶部
                    const card = content.closest('.news-card');
                    if (card) {
                        const headerOffset = 80; // 预留顶部导航栏高度
                        const elementPosition = card.getBoundingClientRect().top;
                        const offsetPosition = elementPosition + window.scrollY - headerOffset;

                        window.scrollTo({
                             top: offsetPosition,
                             behavior: "smooth"
                        });
                    }
                }
            }
        }

        function renderSummaryHtml(summary, id) {
            if (!summary) {
                return `
                    <div class="summary-placeholder">
                        <span>暂无摘要</span>
                        <button class="btn-link" data-action="generate-summary" data-news-id="${id}">生成摘要</button>
                    </div>
                `;
            }

            let cleanSummary = summary;
            try {
                const filterTemp = document.createElement('div');
                filterTemp.innerHTML = summary;
                const images = filterTemp.querySelectorAll('img');
                let changed = false;
                images.forEach(img => {
                    // 检查是否为已知的内部/不可达域名
                    if (img.src && (
                        img.src.includes('ops.xhyun.news.cn') ||
                        img.src.includes('bucket-cb-yunqiao')
                    )) {
                        img.remove();
                        changed = true;
                    }
                });
                if (changed) {
                    cleanSummary = filterTemp.innerHTML;
                }
            } catch (e) {
                console.warn('Summary cleanup failed', e);
            }

            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = cleanSummary;
            const textLength = tempDiv.innerText.length;
            const threshold = 300;

            if (textLength <= threshold) {
                return cleanSummary + ` <button class="btn-regen" data-action="generate-summary" data-news-id="${id}">重新生成</button>`;
            } else {
                return `
                    <div id="summary-content-${id}" class="summary-content collapsed">
                        ${cleanSummary}
                    </div>
                    <button id="btn-expand-${id}" class="btn-link" data-action="toggle-summary" data-news-id="${id}" data-expand="true">查看全部</button>
                    <button id="btn-collapse-${id}" class="btn-link is-hidden" data-action="toggle-summary" data-news-id="${id}" data-expand="false">收起</button>
                    <button class="btn-regen" data-action="generate-summary" data-news-id="${id}">重新生成</button>
                `;
            }
        }

        async function genSummary(id) {
            if (state.isGenerating) return;

            const box = document.getElementById(`summary-${id}`);
            if (!box) return;

            state.isGenerating = true;
            updateRegenButtonsState();

            const originalContent = box.innerHTML;
            const isRegen = box.querySelector('.btn-regen') !== null;

            if (isRegen) {
                const btn = box.querySelector('.btn-regen');
                if (btn) btn.innerText = "生成中...";
            } else {
                box.innerHTML = '<span class="summary-loading">正在生成摘要...</span>';
            }

            try {
                const res = await fetch(`/api/generate_summary/${id}`, { method: 'POST' });
                const data = await res.json();
                if (data.summary) {
                    box.innerHTML = renderSummaryHtml(data.summary, id);

                    const item = state.list.find(i => i.id === id);
                    if (item) item.summary = data.summary;
                } else {
                    throw new Error("Empty summary");
                }
            } catch (err) {
                alert('生成失败');
                box.innerHTML = originalContent;
            } finally {
                state.isGenerating = false;
                updateRegenButtonsState();
            }
        }

        document.addEventListener('click', (event) => {
            const actionEl = event.target.closest('[data-action]');
            if (!actionEl) return;
            const action = actionEl.dataset.action;
            if (action === 'retry-fetch-news') {
                fetchNews(true);
                return;
            }
            if (action === 'generate-summary') {
                genSummary(Number(actionEl.dataset.newsId || 0));
                return;
            }
            if (action === 'toggle-summary') {
                toggleSummary(Number(actionEl.dataset.newsId || 0), actionEl.dataset.expand === 'true');
                return;
            }
            if (action === 'open-news-detail') {
                openNewsDetail(Number(actionEl.dataset.newsId || 0), { replace: actionEl.dataset.replace === 'true' });
                return;
            }
            if (action === 'trace-event') {
                const id = Number(actionEl.dataset.newsId || 0);
                const title = actionEl.dataset.title || '';
                const params = new URLSearchParams();
                if (id) params.set('news_id', id);
                if (title) params.set('event', title);
                window.location.href = `/trace?${params.toString()}`;
                return;
            }
            if (action === 'term-range') {
                changeIndexTermAnalysisRange(actionEl.dataset.range || '30d');
            }
        });

        function updateRegenButtonsState() {
            const regenBtns = document.querySelectorAll('.btn-regen');
            const createBtns = document.querySelectorAll('.summary-placeholder .btn-link');

            const setDisabled = (btn) => {
                btn.disabled = state.isGenerating;
                if (state.isGenerating) btn.style.opacity = '0.5';
                else btn.style.opacity = '1';
                btn.style.cursor = state.isGenerating ? 'not-allowed' : 'pointer';
            };

            regenBtns.forEach(setDisabled);
            createBtns.forEach(setDisabled);
        }

        init();
