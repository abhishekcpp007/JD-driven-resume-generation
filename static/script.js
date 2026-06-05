document.addEventListener('DOMContentLoaded', () => {
    const baseResume = document.getElementById('base-resume');
    const jobDescription = document.getElementById('job-description');
    const templateSelect = document.getElementById('template');
    const generateBtn = document.getElementById('generate-btn');
    const btnText = document.querySelector('.btn-text');
    const loader = document.querySelector('.loader');
    const errorMsg = document.getElementById('error-msg');
    const pdfViewer = document.getElementById('pdf-viewer');
    const emptyState = document.getElementById('empty-state');
    const dryRunToggle = document.getElementById('dry-run-toggle');

    const scorePanel = document.getElementById('score-panel');
    const scoreBadge = document.getElementById('score-badge');
    const scoreMeta = document.getElementById('score-meta');
    const missingList = document.getElementById('missing-list');
    const missingPanel = document.getElementById('missing-panel');
    const toggleMissing = document.getElementById('toggle-missing');
    const aiWarning = document.getElementById('ai-warning');

    function scoreColorClass(score) {
        if (score >= 90) return 'score-great';
        if (score >= 75) return 'score-good';
        if (score >= 50) return 'score-mid';
        return 'score-low';
    }

    function renderScore(result) {
        const score = Number(result.ats_score) || 0;
        scoreBadge.textContent = `ATS Score: ${score}%`;
        scoreBadge.className = 'score-badge ' + scoreColorClass(score);
        const matched = result.matched_keywords || [];
        const missing = result.missing_keywords || [];
        const pageCount = Number(result.page_count) || 0;
        const pageBit = pageCount > 0
            ? ` • ${pageCount} page${pageCount === 1 ? '' : 's'}${pageCount === 1 ? ' ✓' : ' ⚠'}`
            : '';
        scoreMeta.textContent =
            `${matched.length} matched / ${missing.length} missing${pageBit}`;

        missingList.innerHTML = missing.length
            ? missing.map(k => `<span class="kw-chip">${k.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</span>`).join('')
            : '<span class="kw-chip kw-chip-ok">All detected JD keywords covered.</span>';

        missingPanel.style.display = 'none';
        toggleMissing.textContent = 'Show missing keywords';

        if (result.warning) {
            aiWarning.textContent = result.warning;
            aiWarning.style.display = 'block';
        } else {
            aiWarning.style.display = 'none';
            aiWarning.textContent = '';
        }
        scorePanel.style.display = 'block';
    }

    if (toggleMissing) {
        toggleMissing.addEventListener('click', () => {
            const shown = missingPanel.style.display === 'block';
            missingPanel.style.display = shown ? 'none' : 'block';
            toggleMissing.textContent = shown ? 'Show missing keywords' : 'Hide missing keywords';
        });
    }

    // Fetch initial data
    fetch('/api/data')
        .then(response => response.json())
        .then(data => {
            baseResume.value = data.base_resume || '';
            jobDescription.value = data.job_description || '';
            if (data.template) {
                templateSelect.value = data.template;
            }
        })
        .catch(err => console.error("Failed to load initial data:", err));

    generateBtn.addEventListener('click', async () => {
        // UI Reset
        errorMsg.style.display = 'none';
        scorePanel.style.display = 'none';
        generateBtn.disabled = true;
        btnText.textContent = 'Generating...';
        loader.style.display = 'block';
        pdfViewer.style.display = 'none';
        emptyState.style.display = 'block';
        emptyState.textContent = 'Processing with AI...';

        const payload = {
            base_resume: baseResume.value,
            job_description: jobDescription.value,
            template: templateSelect.value,
            dry_run: !!(dryRunToggle && dryRunToggle.checked),
        };

        try {
            const response = await fetch('/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const result = await response.json();

            if (result.success) {
                pdfViewer.src = result.pdf_url + '?t=' + new Date().getTime();
                emptyState.style.display = 'none';
                pdfViewer.style.display = 'block';
                renderScore(result);
            } else {
                errorMsg.textContent = result.error || 'An error occurred during generation.';
                errorMsg.style.display = 'block';
                emptyState.textContent = 'Generation failed.';
            }
        } catch (error) {
            errorMsg.textContent = 'Network error or server crash.';
            errorMsg.style.display = 'block';
            emptyState.textContent = 'Generation failed.';
        } finally {
            generateBtn.disabled = false;
            btnText.textContent = 'Generate ATS Resume';
            loader.style.display = 'none';
        }
    });
});
