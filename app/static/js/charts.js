(() => {
  let charts = [];
  let loading = false;
  const error = document.getElementById('chart-error');
  const categoryEmpty = document.getElementById('category-empty');
  const retry = document.getElementById('retry-charts');
  const font = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const colors = () => {
    const style = getComputedStyle(document.documentElement);
    const value = name => style.getPropertyValue(name).trim();
    return {ink: value('--ink'), muted: value('--muted'), line: value('--line'), surface: value('--surface'), income: value('--success')};
  };
  function emptyChart(canvas, message, category = false) {
    if (!canvas) return;
    const wrapper = canvas.closest('.chart-wrap');
    wrapper.hidden = true;
    if (category && categoryEmpty) { categoryEmpty.classList.remove('d-none'); return; }
    const state = document.createElement('div'); state.className = 'empty-state chart-empty';
    const title = document.createElement('strong'); title.textContent = 'No activity in this period';
    const text = document.createElement('p'); text.textContent = message;
    state.append(title, text); wrapper.after(state);
  }
  function options(money, legend = false, category = false) {
    const color = colors();
    const axis = horizontal => ({
      border: {display: false},
      grid: {display: !horizontal, color: color.line, drawTicks: false},
      ticks: {color: color.muted, padding: 8, maxTicksLimit: horizontal ? 6 : 4, maxRotation: 0, font: {family: font, size: 11}},
      ...(horizontal ? {} : {beginAtZero: true})
    });
    return {
      responsive: true, maintainAspectRatio: false,
      animation: reduced.matches ? false : {duration: 180},
      interaction: {mode: category ? 'nearest' : 'index', intersect: false},
      plugins: {
        legend: {display: legend, position: 'bottom', labels: {color: color.muted, boxWidth: 8, boxHeight: 8, padding: 12, font: {family: font, size: 11}}},
        tooltip: {
          backgroundColor: color.surface, titleColor: color.ink, bodyColor: color.ink,
          borderColor: color.line, borderWidth: 1, cornerRadius: 4, padding: 10, displayColors: false,
          titleFont: {family: font, size: 12, weight: '600'}, bodyFont: {family: font, size: 12},
          callbacks: {label: context => `${context.dataset.label || context.label}: ${money.format(typeof context.parsed === 'number' ? context.parsed : context.chart.options.indexAxis === 'y' ? context.parsed.x : context.parsed.y)}`}
        }
      },
      ...(category ? {} : {scales: {x: axis(true), y: axis(false)}})
    };
  }
  function recolor() {
    const color = colors();
    charts.forEach(chart => {
      const plugin = chart.options.plugins;
      plugin.legend.labels.color = color.muted;
      Object.assign(plugin.tooltip, {backgroundColor: color.surface, titleColor: color.ink, bodyColor: color.ink, borderColor: color.line});
      if (chart.options.indexAxis === 'y') {
        chart.data.datasets[0].backgroundColor = color.ink;
        chart.options.scales.x.ticks.color = color.muted;
        chart.options.scales.y.ticks.color = color.muted;
        chart.options.scales.x.grid.color = color.line;
        chart.data.datasets[0].borderColor = color.surface;
      } else {
        chart.data.datasets.forEach(dataset => { dataset.borderColor = dataset.label === 'Income' ? color.income : color.ink; dataset.pointBackgroundColor = dataset.borderColor; });
        ['x', 'y'].forEach(name => { chart.options.scales[name].ticks.color = color.muted; chart.options.scales[name].grid.color = color.line; });
      }
      chart.update('none');
    });
  }
  async function loadCharts() {
    if (loading) return;
    loading = true; if (retry) retry.disabled = true;
    if (error) error.classList.add('d-none');
    charts.forEach(chart => chart.destroy()); charts = [];
    document.querySelectorAll('.chart-empty').forEach(state => state.remove());
    categoryEmpty?.classList.add('d-none');
    document.querySelectorAll('.chart-wrap').forEach(wrapper => { wrapper.hidden = false; wrapper.classList.add('chart-loading'); wrapper.setAttribute('aria-busy', 'true'); });
    try {
      if (typeof Chart === 'undefined') throw new Error('Chart library unavailable');
      const response = await fetch('/reports/chart-data' + window.location.search, {headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error('Chart data unavailable');
      const data = await response.json();
      if (!Array.isArray(data.labels) || !Array.isArray(data.expense) || !Array.isArray(data.income)) throw new Error('Chart data unavailable');
      const money = new Intl.NumberFormat('en-IN', {style: 'currency', currency: data.currency || 'INR'});
      const labels = data.labels.map(label => {
        const [year, month] = label.split('-');
        return new Date(Number(year), Number(month) - 1, 1).toLocaleDateString('en-IN', {month: 'short', year: '2-digit'});
      });
      const color = colors();
      const categoryCanvas = document.getElementById('category-chart');
      if (categoryCanvas && data.category_values?.some(value => value !== 0)) {
        charts.push(new Chart(categoryCanvas, {type: 'bar', data: {labels: data.category_labels, datasets: [{label: 'Net spending', data: data.category_values, backgroundColor: color.ink, borderWidth: 0, barThickness: 14}]}, options: {...options(money), indexAxis: 'y', scales: {x: {beginAtZero: true, border: {display: false}, grid: {color: color.line, drawTicks: false}, ticks: {color: color.muted, maxTicksLimit: 4, font: {family: font, size: 11}}}, y: {border: {display: false}, grid: {display: false}, ticks: {color: color.muted, font: {family: font, size: 12}}}}}}));
      } else emptyChart(categoryCanvas, '', true);
      const series = (label, values, income = false) => ({label, data: values, borderColor: income ? color.income : color.ink, pointBackgroundColor: income ? color.income : color.ink, backgroundColor: 'transparent', fill: false, tension: .15, borderWidth: 2, pointRadius: 0, pointHoverRadius: 3});
      const trendCanvas = document.getElementById('trend-chart');
      if (trendCanvas && data.expense.some(value => value !== 0)) charts.push(new Chart(trendCanvas, {type: 'line', data: {labels, datasets: [series('Spending', data.expense)]}, options: options(money)}));
      else emptyChart(trendCanvas, 'Add an expense to build your monthly spending rhythm.');
      const comparisonCanvas = document.getElementById('comparison-chart');
      if (comparisonCanvas && [...data.income, ...data.expense].some(value => value !== 0)) charts.push(new Chart(comparisonCanvas, {type: 'line', data: {labels, datasets: [series('Income', data.income, true), series('Spending', data.expense)]}, options: options(money, true)}));
      else emptyChart(comparisonCanvas, 'Add income or expenses to compare your cash flow.');
    } catch (_) {
      if (error) error.classList.remove('d-none');
      document.querySelectorAll('.chart-wrap').forEach(wrapper => { wrapper.hidden = true; });
    } finally {
      document.querySelectorAll('.chart-wrap').forEach(wrapper => { wrapper.classList.remove('chart-loading'); wrapper.setAttribute('aria-busy', 'false'); });
      loading = false; if (retry) retry.disabled = false;
    }
  }
  retry?.addEventListener('click', loadCharts);
  window.addEventListener('themechange', recolor);
  reduced.addEventListener?.('change', () => charts.forEach(chart => { chart.options.animation = reduced.matches ? false : {duration: 180}; chart.update('none'); }));
  loadCharts();
})();
