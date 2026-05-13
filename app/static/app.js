'use strict';

const API = '/api/v1';

function formatBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(1) + ' MB';
}

function fmtDate(s) {
  if (!s) return '—';
  return new Date(s).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function fmtConf(v) {
  if (v == null) return '—';
  return (v * 100).toFixed(1) + '%';
}

function confColor(v) {
  if (v == null) return '#9b9b96';
  if (v >= 0.85) return '#3B6D11';
  if (v >= 0.70) return '#854F0B';
  return '#A32D2D';
}

function fieldLabel(k) {
  return String(k).replaceAll('_', ' ');
}

function formatObjectField(v) {
  if ('present' in v) {
    if (!v.present) return '—';
    return v.snippet || 'present';
  }
  if ('name' in v && 'role' in v) {
    return [v.name, v.role ? `(${v.role})` : ''].filter(Boolean).join(' ');
  }
  if ('name' in v && 'date' in v) {
    return [v.name, v.date].filter(Boolean).join(' - ');
  }
  if ('type' in v && 'jurisdiction' in v) {
    return [v.type, v.jurisdiction].filter(Boolean).join(' - ');
  }

  const parts = Object.entries(v)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${fieldLabel(key)}: ${formatFieldValue(value)}`);
  return parts.length ? parts.join('\n') : '—';
}

function formatFieldValue(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) {
    const parts = v.map(formatFieldValue).filter((part) => part && part !== '—');
    return parts.length ? parts.join('\n') : '—';
  }
  if (typeof v === 'object') return formatObjectField(v);
  return String(v);
}

function statusBadge(s) {
  const map = {
    completed: 'badge-success',
    processing: 'badge-info',
    queued: 'badge-warn',
    failed: 'badge-error',
    review_required: 'badge-orange',
    uploaded: 'badge-gray',
  };
  return map[s] || 'badge-gray';
}

document.addEventListener('alpine:init', () => {
  Alpine.data('docintel', function () {
    return {
      view: 'dashboard',
      apiKey: localStorage.getItem('di_api_key') || '',
      metrics: null,
      recentDocs: [],
      dashLoading: false,
      dashError: '',
      dragover: false,
      uploadFiles: [],
      uploading: false,
      uploadResults: [],
      docs: [],
      docsTotal: 0,
      docsPage: 0,
      docsPerPage: 20,
      docsLoading: false,
      docsError: '',
      filterStatus: '',
      filterType: '',
      searchQ: '',
      showModal: false,
      selDoc: null,
      selResult: null,
      selHistory: [],
      modalLoading: false,
      reviewTasks: [],
      reviewLoading: false,
      reviewError: '',
      selTask: null,
      reviewerName: localStorage.getItem('di_reviewer') || '',
      correctedValue: '',
      reviewComment: '',
      reviewSubmitting: false,
      analyticsLoading: false,
      analyticsError: '',
      analyticsMetrics: null,
      ocrDist: null,
      corrStats: null,
      preferences: [],
      drafts: [],
      draftType: 'internal_memo',
      draftLoading: false,
      draftReviewer: localStorage.getItem('di_draft_reviewer') || '',
      draftEditKey: '',
      draftEditedContent: '',
      _charts: {},

      async init() {
        this.$watch('apiKey', v => localStorage.setItem('di_api_key', v));
        this.$watch('reviewerName', v => localStorage.setItem('di_reviewer', v));
        this.$watch('draftReviewer', v => localStorage.setItem('di_draft_reviewer', v));
        this._go('dashboard');
      },

      _go(v) {
        this.view = v;
        if (v === 'dashboard') this._loadDashboard();
        if (v === 'documents') this._loadDocs(0);
        if (v === 'review') this._loadReview();
        if (v === 'analytics') this._loadAnalytics();
      },

      async _fetch(path, opts = {}) {
        const headers = { ...opts.headers };
        if (this.apiKey) headers['X-API-Key'] = this.apiKey;
        const r = await fetch(API + path, { ...opts, headers });
        if (r.status === 204) return null;
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        return data;
      },

      async _loadDashboard() {
        this.dashLoading = true;
        this.dashError = '';
        try {
          const [m, d] = await Promise.all([
            this._fetch('/analytics/metrics/overview'),
            this._fetch('/documents?limit=5'),
          ]);
          this.metrics = m;
          this.recentDocs = d?.items || [];
        } catch (e) {
          this.dashError = e.message;
        }
        this.dashLoading = false;
      },

      handleDrop(e) {
        e.preventDefault();
        this.dragover = false;
        const files = e.dataTransfer?.files || e.target.files;
        if (files) this.uploadFiles.push(...Array.from(files));
      },

      handlePick(e) {
        if (e.target.files) this.uploadFiles.push(...Array.from(e.target.files));
        e.target.value = '';
      },

      removeFile(i) {
        this.uploadFiles.splice(i, 1);
      },

      async uploadAll() {
        if (!this.uploadFiles.length || this.uploading) return;
        this.uploading = true;
        this.uploadResults = [];
        for (const f of this.uploadFiles) {
          const fd = new FormData();
          fd.append('file', f);
          try {
            const h = {};
            if (this.apiKey) h['X-API-Key'] = this.apiKey;
            const r = await fetch(API + '/documents/upload', { method: 'POST', headers: h, body: fd });
            const data = await r.json();
            if (r.ok) {
              this.uploadResults.push({ name: f.name, ok: true, id: data.document?.id, taskId: data.task_id });
            } else {
              this.uploadResults.push({ name: f.name, ok: false, error: data.detail || 'Upload failed' });
            }
          } catch (e) {
            this.uploadResults.push({ name: f.name, ok: false, error: e.message });
          }
        }
        this.uploadFiles = [];
        this.uploading = false;
      },

      async _loadDocs(page = 0) {
        this.docsLoading = true;
        this.docsError = '';
        this.docsPage = page;
        try {
          if (this.searchQ.length >= 2) {
            const items = await this._fetch(`/documents/search?q=${encodeURIComponent(this.searchQ)}&limit=50`);
            this.docs = items || [];
            this.docsTotal = this.docs.length;
          } else {
            const p = new URLSearchParams({ limit: this.docsPerPage, offset: page * this.docsPerPage });
            if (this.filterStatus) p.set('status', this.filterStatus);
            if (this.filterType) p.set('document_type', this.filterType);
            const data = await this._fetch('/documents?' + p);
            this.docs = data?.items || [];
            this.docsTotal = data?.total ?? 0;
          }
        } catch (e) {
          this.docsError = e.message;
        }
        this.docsLoading = false;
      },

      async searchDocs() {
        await this._loadDocs(0);
      },

      get docsPageCount() {
        return Math.max(1, Math.ceil(this.docsTotal / this.docsPerPage));
      },

      async openDoc(doc) {
        this.selDoc = doc;
        this.selResult = null;
        this.selHistory = [];
        this.drafts = [];
        this.draftEditKey = '';
        this.showModal = true;
        this.modalLoading = true;
        try {
          const [res, hist, drafts] = await Promise.all([
            this._fetch(`/documents/${doc.id}/result`).catch(() => null),
            this._fetch(`/documents/${doc.id}/history`).catch(() => []),
            this._fetch(`/documents/${doc.id}/drafts`).catch(() => []),
          ]);
          this.selResult = res;
          this.selHistory = Array.isArray(hist) ? hist : [];
          this.drafts = Array.isArray(drafts) ? drafts : [];
        } catch (_) {
        }
        this.modalLoading = false;
      },

      closeModal() {
        this.showModal = false;
        this.selDoc = null;
        this.drafts = [];
      },

      async loadDrafts() {
        if (!this.selDoc) return;
        this.draftLoading = true;
        try {
          this.drafts = await this._fetch(`/documents/${this.selDoc.id}/drafts`) || [];
        } catch (e) {
          alert('Draft load failed: ' + e.message);
        }
        this.draftLoading = false;
      },

      async generateDraft() {
        if (!this.selDoc || this.draftLoading) return;
        this.draftLoading = true;
        try {
          await this._fetch(`/documents/${this.selDoc.id}/drafts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ draft_type: this.draftType }),
          });
          await this.loadDrafts();
        } catch (e) {
          alert('Draft generation failed: ' + e.message);
        }
        this.draftLoading = false;
      },

      startDraftEdit(draft, section) {
        this.draftEditKey = draft.id + ':' + section.key;
        this.draftEditedContent = section.content || '';
      },

      async submitDraftEdit(draft, section) {
        if (!this.selDoc || !this.draftReviewer.trim()) return;
        try {
          await this._fetch(`/documents/${this.selDoc.id}/drafts/${draft.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              reviewer_name: this.draftReviewer.trim(),
              sections: [{ key: section.key, edited_content: this.draftEditedContent }],
            }),
          });
          this.draftEditKey = '';
          await this.loadDrafts();
        } catch (e) {
          alert('Draft edit failed: ' + e.message);
        }
      },

      async deleteDoc(id) {
        if (!confirm('Delete this document? This cannot be undone.')) return;
        try {
          await this._fetch(`/documents/${id}`, { method: 'DELETE' });
          if (this.showModal) this.closeModal();
          await this._loadDocs(this.docsPage);
        } catch (e) {
          alert('Delete failed: ' + e.message);
        }
      },

      async reprocess(id) {
        try {
          await this._fetch(`/documents/${id}/reprocess`, { method: 'POST' });
          await this._loadDocs(this.docsPage);
          if (this.showModal && this.selDoc?.id === id) this.closeModal();
        } catch (e) {
          alert('Reprocess failed: ' + e.message);
        }
      },

      async _loadReview() {
        this.reviewLoading = true;
        this.reviewError = '';
        this.selTask = null;
        try {
          this.reviewTasks = await this._fetch('/reviews/pending') || [];
        } catch (e) {
          this.reviewError = e.message;
        }
        this.reviewLoading = false;
      },

      selectTask(t) {
        this.selTask = t;
        this.correctedValue = String(t.proposed_value?.value ?? '');
        this.reviewComment = '';
      },

      async submitReview() {
        if (!this.selTask || !this.reviewerName.trim() || this.reviewSubmitting) return;
        this.reviewSubmitting = true;
        try {
          await this._fetch(`/reviews/${this.selTask.id}/decision`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              reviewer_name: this.reviewerName.trim(),
              corrected_value: { value: this.correctedValue },
              comment: this.reviewComment || null,
            }),
          });
          this.selTask = null;
          await this._loadReview();
        } catch (e) {
          alert('Submit failed: ' + e.message);
        }
        this.reviewSubmitting = false;
      },

      async _loadAnalytics() {
        this.analyticsLoading = true;
        this.analyticsError = '';
        try {
          const [m, ocr, corr, prefs] = await Promise.all([
            this._fetch('/analytics/metrics/overview'),
            this._fetch('/analytics/metrics/ocr-distribution'),
            this._fetch('/analytics/corrections/stats'),
            this._fetch('/preferences').catch(() => []),
          ]);
          this.analyticsMetrics = m;
          this.ocrDist = ocr;
          this.corrStats = corr;
          this.preferences = Array.isArray(prefs) ? prefs : [];
          this.$nextTick(() => this._renderCharts());
        } catch (e) {
          this.analyticsError = e.message;
        }
        this.analyticsLoading = false;
      },

      async deletePreference(id) {
        if (!confirm('Delete this learned preference?')) return;
        try {
          await this._fetch(`/preferences/${id}`, { method: 'DELETE' });
          this.preferences = this.preferences.filter(p => p.id !== id);
        } catch (e) {
          alert('Delete failed: ' + e.message);
        }
      },

      _renderCharts() {
        if (typeof Chart === 'undefined') return;

        const ocrCanvas = document.getElementById('chart-ocr');
        if (ocrCanvas && this.ocrDist?.buckets) {
          if (this._charts.ocr) this._charts.ocr.destroy();
          const buckets = this.ocrDist.buckets;
          this._charts.ocr = new Chart(ocrCanvas, {
            type: 'bar',
            data: {
              labels: Object.keys(buckets),
              datasets: [{
                data: Object.values(buckets),
                backgroundColor: 'rgba(24,95,165,.55)',
                borderColor: 'rgba(24,95,165,1)',
                borderWidth: 1,
              }],
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } },
              scales: { y: { beginAtZero: true, ticks: { stepSize: 1, precision: 0 } } },
            },
          });
        }

        const corrCanvas = document.getElementById('chart-corrections');
        if (corrCanvas && this.corrStats?.by_field) {
          if (this._charts.corr) this._charts.corr.destroy();
          const entries = Object.entries(this.corrStats.by_field).sort((a, b) => b[1] - a[1]).slice(0, 8);
          this._charts.corr = new Chart(corrCanvas, {
            type: 'bar',
            data: {
              labels: entries.map(([k]) => k),
              datasets: [{
                data: entries.map(([, v]) => v),
                backgroundColor: 'rgba(163,45,45,.55)',
                borderColor: 'rgba(163,45,45,1)',
                borderWidth: 1,
              }],
            },
            options: {
              indexAxis: 'y',
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } },
              scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
            },
          });
        }

        const typeCanvas = document.getElementById('chart-types');
        if (typeCanvas && this.analyticsMetrics?.by_document_type) {
          if (this._charts.types) this._charts.types.destroy();
          const types = this.analyticsMetrics.by_document_type;
          this._charts.types = new Chart(typeCanvas, {
            type: 'doughnut',
            data: {
              labels: Object.keys(types),
              datasets: [{
                data: Object.values(types),
                backgroundColor: ['rgba(24,95,165,.7)', 'rgba(29,158,117,.7)', 'rgba(186,117,23,.7)', 'rgba(163,45,45,.7)'],
                borderWidth: 1,
              }],
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 12 } } } },
            },
          });
        }
      },

      sb: statusBadge,
      fmtDate,
      fmtConf,
      confColor,
      formatBytes,
      fmtField: formatFieldValue,
    };
  });
});
