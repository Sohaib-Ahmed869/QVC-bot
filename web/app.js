const API_BASE = '';

// ============================================
// State
// ============================================
let applicants = [];
let selectedIds = new Set();
let schedule = { enabled: true, days: [] };
let settings = { max_parallel: 2 };
let sessions = [];
let selectedCenter = 'Islamabad';
let isRunning = false;
let slotFound = false;
let slotDetails = null;
let logCursor = 0;
let pollingInterval = null;

const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

// ============================================
// Initialization
// ============================================
document.addEventListener('DOMContentLoaded', () => {
    loadApplicants();
    loadSchedule();
    loadSettings();
    loadCenter();
    setupEventListeners();
    checkInitialStatus();
    checkScheduledRun();
});

function setupEventListeners() {
    document.getElementById('addApplicantBtn').addEventListener('click', () => openModal());
    document.getElementById('modalClose').addEventListener('click', closeModal);
    document.getElementById('cancelBtn').addEventListener('click', closeModal);
    document.getElementById('modalOverlay').addEventListener('click', (e) => {
        if (e.target === document.getElementById('modalOverlay')) closeModal();
    });
    document.getElementById('applicantForm').addEventListener('submit', handleFormSubmit);

    const mobileInput = document.getElementById('mobile');
    mobileInput.addEventListener('input', validateMobileInput);
    mobileInput.addEventListener('blur', validateMobileInput);

    document.getElementById('addDayBtn').addEventListener('click', addScheduleDay);
    document.getElementById('scheduleEnabled').addEventListener('change', (e) => {
        schedule.enabled = e.target.checked;
        saveSchedule();
    });

    document.querySelectorAll('input[name="center"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            selectedCenter = e.target.value;
            saveCenter();
        });
    });

    document.getElementById('maxParallel').addEventListener('change', (e) => {
        settings.max_parallel = parseInt(e.target.value);
        saveSettings();
        if (selectedIds.size > settings.max_parallel) {
            selectedIds = new Set(Array.from(selectedIds).slice(0, settings.max_parallel));
            renderApplicants();
            updateSelectionUI();
        }
    });

    document.getElementById('runBotBtn').addEventListener('click', runBot);
    document.getElementById('stopBotBtn').addEventListener('click', stopBot);
    document.getElementById('runSelectedBtn').addEventListener('click', runSelected);
    document.getElementById('clearSelectionBtn').addEventListener('click', clearSelection);
    document.getElementById('clearLogsBtn').addEventListener('click', clearLogs);
    document.getElementById('dismissSlotAlert').addEventListener('click', dismissSlotAlert);

    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
}

// ============================================
// API Functions
// ============================================
async function loadApplicants() {
    try {
        const response = await fetch(`${API_BASE}/api/applicants`);
        if (response.ok) {
            const data = await response.json();
            applicants = data.applicants || [];
            renderApplicants();
        }
    } catch (error) {
        console.error('Failed to load applicants:', error);
        const stored = localStorage.getItem('applicants');
        if (stored) { applicants = JSON.parse(stored); renderApplicants(); }
    }
}

async function saveApplicant(applicant) {
    try {
        const isNew = !applicant.id;
        const method = isNew ? 'POST' : 'PUT';
        const url = isNew ? `${API_BASE}/api/applicants` : `${API_BASE}/api/applicants/${applicant.id}`;
        const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(applicant) });
        if (response.ok) {
            const saved = await response.json();
            if (isNew) applicants.push(saved);
            else { const idx = applicants.findIndex(a => a.id === saved.id); if (idx >= 0) applicants[idx] = saved; }
            renderApplicants();
            showToast(isNew ? 'Applicant added' : 'Applicant updated', 'success');
            return true;
        }
    } catch (error) {
        if (!applicant.id) { applicant.id = generateId(); applicants.push(applicant); }
        else { const idx = applicants.findIndex(a => a.id === applicant.id); if (idx >= 0) applicants[idx] = applicant; }
        localStorage.setItem('applicants', JSON.stringify(applicants));
        renderApplicants();
        showToast('Saved locally', 'success');
        return true;
    }
    return false;
}

async function deleteApplicant(id) {
    try {
        const response = await fetch(`${API_BASE}/api/applicants/${id}`, { method: 'DELETE' });
        if (response.ok) {
            applicants = applicants.filter(a => a.id !== id);
            selectedIds.delete(id);
            renderApplicants();
            updateSelectionUI();
            showToast('Applicant removed', 'success');
            return true;
        }
    } catch (error) {
        applicants = applicants.filter(a => a.id !== id);
        selectedIds.delete(id);
        localStorage.setItem('applicants', JSON.stringify(applicants));
        renderApplicants();
        updateSelectionUI();
        showToast('Removed locally', 'success');
        return true;
    }
    return false;
}

async function resetApplicant(id) {
    try {
        const response = await fetch(`${API_BASE}/api/applicants/${id}/reset`, { method: 'POST' });
        if (response.ok) {
            const updated = await response.json();
            const idx = applicants.findIndex(a => a.id === id);
            if (idx >= 0) applicants[idx] = updated;
            renderApplicants();
            showToast('Applicant reset to pending', 'success');
            return true;
        }
    } catch (error) {
        const idx = applicants.findIndex(a => a.id === id);
        if (idx >= 0) { applicants[idx].status = 'pending'; localStorage.setItem('applicants', JSON.stringify(applicants)); renderApplicants(); showToast('Reset locally', 'success'); return true; }
    }
    return false;
}

async function loadSchedule() {
    try {
        const response = await fetch(`${API_BASE}/api/schedule`);
        if (response.ok) {
            const data = await response.json();
            if (data.days) schedule = data;
            else if (data.start_time) schedule = { enabled: data.enabled, days: [{ day: 'Daily', slots: [{ start: data.start_time, end: data.end_time }] }] };
        }
    } catch (error) {
        const stored = localStorage.getItem('schedule');
        if (stored) schedule = JSON.parse(stored);
    }
    document.getElementById('scheduleEnabled').checked = schedule.enabled !== false;
    renderSchedule();
}

async function loadSettings() {
    try {
        const response = await fetch(`${API_BASE}/api/settings`);
        if (response.ok) {
            settings = await response.json();
            document.getElementById('maxParallel').value = settings.max_parallel || 2;
        }
    } catch (error) { console.error('Failed to load settings:', error); }
}

async function saveSettings() {
    try {
        await fetch(`${API_BASE}/api/settings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) });
        showToast(`Max parallel sessions set to ${settings.max_parallel}`, 'success');
    } catch (error) { console.error('Failed to save settings:', error); }
}

let saveScheduleTimeout = null;
async function saveSchedule() {
    if (saveScheduleTimeout) clearTimeout(saveScheduleTimeout);
    saveScheduleTimeout = setTimeout(async () => {
        try { await fetch(`${API_BASE}/api/schedule`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(schedule) }); }
        catch (error) { localStorage.setItem('schedule', JSON.stringify(schedule)); }
    }, 500);
}

function loadCenter() {
    const stored = localStorage.getItem('selectedCenter');
    if (stored) { selectedCenter = stored; const radio = document.querySelector(`input[name="center"][value="${stored}"]`); if (radio) radio.checked = true; }
}

function saveCenter() { localStorage.setItem('selectedCenter', selectedCenter); addLog('Center changed to: ' + selectedCenter); }

// ============================================
// Selection Management
// ============================================
function toggleSelection(id, event) {
    if (isRunning) return;
    if (event) event.stopPropagation();
    if (selectedIds.has(id)) {
        selectedIds.delete(id);
    } else {
        // Limit selection to max_parallel setting
        if (selectedIds.size >= settings.max_parallel) {
            showToast(`Maximum ${settings.max_parallel} applicant(s) can be selected with current parallel setting`, 'warning');
            return;
        }
        selectedIds.add(id);
    }
    renderApplicants();
    updateSelectionUI();
}

function clearSelection() { selectedIds.clear(); renderApplicants(); updateSelectionUI(); }

function updateSelectionUI() {
    const count = selectedIds.size;
    const selectionInfo = document.getElementById('selectionInfo');
    const selectionActions = document.getElementById('selectionActions');
    const selectedCount = document.getElementById('selectedCount');
    if (count > 0 && !isRunning) {
        selectionInfo.textContent = `${count} selected`;
        selectionActions.classList.remove('hidden');
        selectedCount.textContent = count;
    } else {
        selectionInfo.textContent = '';
        selectionActions.classList.add('hidden');
    }
}

// ============================================
// Bot Control
// ============================================
async function runSelected() {
    if (selectedIds.size === 0) {
        showToast('Please select at least one applicant', 'warning');
        return;
    }

    await startBot(Array.from(selectedIds));
}

async function runBot() {
    let applicantIds = Array.from(selectedIds);
    if (applicantIds.length === 0) {
        const pending = applicants.filter(a => a.status === 'pending');
        if (pending.length === 0) { showToast('No pending applicants to run', 'warning'); return; }
        applicantIds = pending.slice(0, settings.max_parallel).map(a => a.id);
    }
    await startBot(applicantIds);
}

async function startBot(applicantIds) {
    try {
        const response = await fetch(`${API_BASE}/api/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ center: selectedCenter, applicant_ids: applicantIds, max_parallel: settings.max_parallel })
        });
        if (response.ok) {
            isRunning = true; slotFound = false; slotDetails = null; logCursor = 0;
            updateStatusIndicator('running');
            document.getElementById('runBotBtn').classList.add('hidden');
            document.getElementById('stopBotBtn').classList.remove('hidden');
            updateSelectionUI();
            addLog(`Bot started with ${applicantIds.length} parallel session(s)`, 'success');
            startStatusPolling();
        } else {
            const error = await response.json();
            showToast(error.detail || 'Failed to start bot', 'error');
        }
    } catch (error) { showToast('Server unavailable', 'error'); addLog('Error: Server unavailable', 'error'); }
}

async function stopBot() {
    try { await fetch(`${API_BASE}/api/stop`, { method: 'POST' }); addLog('Stopping all sessions...', 'info'); } catch (error) { }
    isRunning = false;
    stopStatusPolling();
    updateStatusIndicator('idle');
    document.getElementById('runBotBtn').classList.remove('hidden');
    document.getElementById('stopBotBtn').classList.add('hidden');
    updateSelectionUI();
    renderSessions();
    loadApplicants();
}

// ============================================
// Status Polling
// ============================================
function startStatusPolling() {
    if (pollingInterval) return;
    pollingInterval = setInterval(pollStatus, 2000);
    pollStatus();
}

function stopStatusPolling() { if (pollingInterval) { clearInterval(pollingInterval); pollingInterval = null; } }

async function pollStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/status?log_cursor=${logCursor}`);
        if (response.ok) {
            const status = await response.json();
            isRunning = status.running;
            sessions = status.sessions || [];
            slotFound = status.slot_found;
            slotDetails = status.slot_details;
            if (status.log_cursor !== undefined) logCursor = status.log_cursor;

            renderSessions();

            if (slotFound) {
                updateStatusIndicator('slot-found');
                if (!document.getElementById('slotAlertOverlay').classList.contains('shown')) showSlotFoundAlert();
            } else if (isRunning) {
                updateStatusIndicator('running');
                document.getElementById('runBotBtn').classList.add('hidden');
                document.getElementById('stopBotBtn').classList.remove('hidden');
            } else {
                updateStatusIndicator(schedule.enabled && isInScheduledWindow() ? 'scheduled' : 'idle');
                document.getElementById('runBotBtn').classList.remove('hidden');
                document.getElementById('stopBotBtn').classList.add('hidden');
                loadApplicants();
                stopStatusPolling();
            }

            if (status.applicants) {
                status.applicants.forEach(update => {
                    const idx = applicants.findIndex(a => a.id === update.id);
                    if (idx >= 0) applicants[idx].status = update.status;
                });
                renderApplicants();
            }
            if (status.logs && status.logs.length > 0) status.logs.forEach(log => addLog(log.message, log.type));
        }
    } catch (error) { console.error('Status poll error:', error); stopStatusPolling(); }
}

async function checkInitialStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/status?log_cursor=0`);
        if (response.ok) {
            const status = await response.json();
            isRunning = status.running;
            sessions = status.sessions || [];
            if (isRunning) startStatusPolling();
            updateStatusIndicator(isRunning ? 'running' : 'idle');
            renderSessions();
        }
    } catch (error) { console.error('Failed to check initial status:', error); }
}

function updateStatusIndicator(status) {
    const indicator = document.getElementById('statusIndicator');
    const statusText = indicator.querySelector('.status-text');
    indicator.className = 'status-indicator';

    switch (status) {
        case 'running':
            indicator.classList.add('running');
            const activeCount = sessions.filter(s => !['completed', 'failed', 'stopped'].includes(s.status)).length;
            statusText.textContent = `Running (${activeCount})`;
            break;
        case 'slot-found':
            indicator.classList.add('slot-found');
            statusText.textContent = 'Slot Found!';
            break;
        case 'scheduled':
            indicator.classList.add('scheduled');
            statusText.textContent = 'Scheduled';
            break;
        default:
            statusText.textContent = 'Idle';
    }
}

function checkScheduledRun() {
    setInterval(() => {
        if (schedule.enabled && isInScheduledWindow() && !isRunning && applicants.length > 0) {
            const lastRun = localStorage.getItem('lastScheduledRun');
            const today = new Date().toDateString();
            if (lastRun !== today) { localStorage.setItem('lastScheduledRun', today); addLog('Auto-starting scheduled run...'); runBot(); }
        }
    }, 60000);
}

function isInScheduledWindow() {
    if (!schedule.days || schedule.days.length === 0) return false;
    const now = new Date();
    const currentTime = now.getHours() * 60 + now.getMinutes();
    const currentDayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][now.getDay()];
    for (const dayData of schedule.days) {
        if (dayData.day !== currentDayName && dayData.day !== 'Daily') continue;
        for (const slot of dayData.slots) {
            const [startH, startM] = slot.start.split(':').map(Number);
            const [endH, endM] = slot.end.split(':').map(Number);
            const startMinutes = startH * 60 + startM;
            const endMinutes = endH * 60 + endM;
            if (endMinutes < startMinutes) { if (currentTime >= startMinutes || currentTime < endMinutes) return true; }
            else { if (currentTime >= startMinutes && currentTime < endMinutes) return true; }
        }
    }
    return false;
}

// ============================================
// Rendering Functions
// ============================================
function renderApplicants() {
    const applicantsList = document.getElementById('applicantsList');
    const emptyState = document.getElementById('emptyState');

    if (applicants.length === 0) { emptyState.classList.remove('hidden'); applicantsList.querySelectorAll('.applicant-card').forEach(el => el.remove()); return; }
    emptyState.classList.add('hidden');
    applicantsList.querySelectorAll('.applicant-card').forEach(el => el.remove());
    applicants.forEach((applicant, index) => applicantsList.appendChild(createApplicantCard(applicant, index)));
}

function createApplicantCard(applicant, index) {
    const card = document.createElement('div');
    card.className = 'applicant-card';
    if (applicant.status === 'processing') card.classList.add('processing');
    if (applicant.status === 'completed' || applicant.status === 'slot_found') card.classList.add('slot-found');
    if (applicant.status === 'failed') card.classList.add('failed');
    if (applicant.status === 'no_slot') card.classList.add('no-slot');
    if (selectedIds.has(applicant.id)) card.classList.add('selected');

    const statusBadge = applicant.status !== 'pending' ? `<span class="status-badge ${applicant.status.replace('_', '-')}">${formatStatus(applicant.status)}</span>` : '';

    card.innerHTML = `
        <div class="applicant-checkbox"><input type="checkbox" ${selectedIds.has(applicant.id) ? 'checked' : ''} ${isRunning ? 'disabled' : ''} data-id="${applicant.id}"></div>
        <div class="applicant-number">${index + 1}</div>
        <div class="applicant-info">
            <div class="applicant-passport">${escapeHtml(applicant.passport_number)} ${statusBadge}</div>
            <div class="applicant-details"><span>Visa: ${escapeHtml(applicant.visa_number)}</span><span>${escapeHtml(applicant.email)}</span></div>
        </div>
        <div class="applicant-actions">
            ${applicant.status !== 'pending' ? `<button class="action-btn reset" title="Reset" data-id="${applicant.id}"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 8C2 4.68629 4.68629 2 8 2C10.5 2 12.5 3.5 13.5 5.5M14 8C14 11.3137 11.3137 14 8 14C5.5 14 3.5 12.5 2.5 10.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M13 2V6H9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M3 14V10H7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button>` : ''}
            <button class="action-btn edit" title="Edit" data-id="${applicant.id}"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M11.5 2.5L13.5 4.5L5 13H3V11L11.5 2.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></button>
            <button class="action-btn delete" title="Delete" data-id="${applicant.id}"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 5H12L11.2 14H4.8L4 5Z" stroke="currentColor" stroke-width="1.5"/><path d="M6 5V3H10V5" stroke="currentColor" stroke-width="1.5"/><path d="M2 5H14" stroke="currentColor" stroke-width="1.5"/></svg></button>
        </div>`;

    card.querySelector('input[type="checkbox"]').addEventListener('click', (e) => { e.preventDefault(); toggleSelection(applicant.id, e); });
    card.addEventListener('click', (e) => { if (!e.target.closest('.applicant-actions') && !e.target.closest('.applicant-checkbox')) toggleSelection(applicant.id); });
    const resetBtn = card.querySelector('.reset');
    if (resetBtn) resetBtn.addEventListener('click', (e) => { e.stopPropagation(); resetApplicant(applicant.id); });
    card.querySelector('.edit').addEventListener('click', (e) => { e.stopPropagation(); openModal(applicant); });
    card.querySelector('.delete').addEventListener('click', (e) => { e.stopPropagation(); if (confirm('Remove this applicant?')) deleteApplicant(applicant.id); });
    return card;
}

function formatStatus(status) {
    return { 'pending': 'Pending', 'processing': 'Processing', 'slot_found': 'Slot Found!', 'no_slot': 'No Slot', 'failed': 'Failed', 'completed': 'Completed' }[status] || status;
}

function renderSessions() {
    const panel = document.getElementById('sessionsPanel');
    const grid = document.getElementById('sessionsGrid');
    const summary = document.getElementById('sessionSummary');

    if (sessions.length === 0 && !isRunning) { panel.classList.add('hidden'); return; }
    panel.classList.remove('hidden');

    const active = sessions.filter(s => !['completed', 'failed', 'stopped', 'slot_found'].includes(s.status)).length;
    summary.textContent = `${active} / ${sessions.length} running`;

    grid.innerHTML = sessions.map(session => {
        const statusClass = session.status.replace('_', '-');
        return `<div class="session-card ${statusClass}">
            <div class="session-header"><span class="session-passport">${escapeHtml(session.passport_number)}</span><span class="session-status">${session.status.replace('_', ' ')}</span></div>
            <div class="session-details">
                ${session.ip ? `<div class="session-detail-row"><span class="session-detail-label">IP:</span><span class="session-detail-value">${session.ip}</span></div>` : ''}
                ${session.poll_count > 0 ? `<div class="session-detail-row"><span class="session-detail-label">Polls:</span><span class="session-detail-value">${session.poll_count}</span></div>` : ''}
                ${session.message ? `<div class="session-detail-row"><span class="session-detail-label">Note:</span><span class="session-detail-value">${escapeHtml(session.message)}</span></div>` : ''}
            </div></div>`;
    }).join('');
}

function renderSchedule() {
    const container = document.getElementById('scheduleDays');
    const empty = document.getElementById('scheduleEmpty');
    container.innerHTML = '';
    if (!schedule.days || schedule.days.length === 0) { empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    schedule.days.forEach((dayData, dayIndex) => container.appendChild(createDayCard(dayData, dayIndex)));
}

function createDayCard(dayData, dayIndex) {
    const card = document.createElement('div');
    card.className = 'day-card';
    const header = document.createElement('div');
    header.className = 'day-header';

    const select = document.createElement('select');
    select.className = 'day-select';
    DAYS_OF_WEEK.forEach(day => { const opt = document.createElement('option'); opt.value = day; opt.textContent = day; opt.selected = day === dayData.day; select.appendChild(opt); });
    select.addEventListener('change', (e) => { schedule.days[dayIndex].day = e.target.value; saveSchedule(); });

    const actions = document.createElement('div');
    actions.className = 'day-actions';
    const addSlotBtn = document.createElement('button');
    addSlotBtn.className = 'day-btn';
    addSlotBtn.innerHTML = '+ Add Slot';
    addSlotBtn.addEventListener('click', () => { schedule.days[dayIndex].slots.push({ start: '09:00', end: '17:00' }); renderSchedule(); saveSchedule(); });

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'day-btn delete-day';
    deleteBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 4H11L10.2 12H3.8L3 4Z" stroke="currentColor" stroke-width="1.5"/><path d="M5 4V2.5H9V4" stroke="currentColor" stroke-width="1.5"/><path d="M2 4H12" stroke="currentColor" stroke-width="1.5"/></svg>`;
    deleteBtn.addEventListener('click', () => { schedule.days.splice(dayIndex, 1); renderSchedule(); saveSchedule(); });

    actions.appendChild(addSlotBtn);
    actions.appendChild(deleteBtn);
    header.appendChild(select);
    header.appendChild(actions);

    const slotsContainer = document.createElement('div');
    slotsContainer.className = 'time-slots';
    dayData.slots.forEach((slot, slotIndex) => {
        const slotEl = document.createElement('div');
        slotEl.className = 'time-slot';
        const startInput = document.createElement('input'); startInput.type = 'time'; startInput.className = 'slot-time-input'; startInput.value = slot.start;
        startInput.addEventListener('change', (e) => { schedule.days[dayIndex].slots[slotIndex].start = e.target.value; saveSchedule(); });
        const sep = document.createElement('span'); sep.className = 'slot-separator'; sep.textContent = 'to';
        const endInput = document.createElement('input'); endInput.type = 'time'; endInput.className = 'slot-time-input'; endInput.value = slot.end;
        endInput.addEventListener('change', (e) => { schedule.days[dayIndex].slots[slotIndex].end = e.target.value; saveSchedule(); });
        const delBtn = document.createElement('button'); delBtn.className = 'slot-delete'; delBtn.innerHTML = '×';
        delBtn.addEventListener('click', () => { schedule.days[dayIndex].slots.splice(slotIndex, 1); if (schedule.days[dayIndex].slots.length === 0) schedule.days.splice(dayIndex, 1); renderSchedule(); saveSchedule(); });
        slotEl.appendChild(startInput); slotEl.appendChild(sep); slotEl.appendChild(endInput); slotEl.appendChild(delBtn);
        slotsContainer.appendChild(slotEl);
    });

    card.appendChild(header);
    card.appendChild(slotsContainer);
    return card;
}

function addScheduleDay() {
    const usedDays = schedule.days.map(d => d.day);
    const availableDay = DAYS_OF_WEEK.find(d => !usedDays.includes(d)) || 'Monday';
    schedule.days.push({ day: availableDay, slots: [{ start: '09:00', end: '17:00' }] });
    renderSchedule();
    saveSchedule();
}

// ============================================
// Modal & Form
// ============================================
function openModal(applicant = null) {
    document.getElementById('modalTitle').textContent = applicant ? 'Edit Applicant' : 'Add Applicant';
    if (applicant) {
        document.getElementById('applicantId').value = applicant.id;
        document.getElementById('passportNumber').value = applicant.passport_number;
        document.getElementById('visaNumber').value = applicant.visa_number;
        document.getElementById('mobile').value = applicant.mobile || '';
        document.getElementById('email').value = applicant.email;
    } else {
        document.getElementById('applicantForm').reset();
        document.getElementById('applicantId').value = '';
    }
    document.getElementById('modalOverlay').classList.remove('hidden');
    document.getElementById('passportNumber').focus();
}

function closeModal() { document.getElementById('modalOverlay').classList.add('hidden'); document.getElementById('applicantForm').reset(); }

async function handleFormSubmit(e) {
    e.preventDefault();
    const mobileValue = document.getElementById('mobile').value;
    if (!isValidMobile(mobileValue)) { showMobileError(); document.getElementById('mobile').focus(); return; }
    const id = document.getElementById('applicantId').value;
    const applicant = {
        id: id || null,
        country: 'Pakistan',
        passport_number: document.getElementById('passportNumber').value.toUpperCase().trim(),
        visa_number: document.getElementById('visaNumber').value.toUpperCase().trim(),
        mobile: mobileValue,
        email: document.getElementById('email').value.toLowerCase().trim(),
        status: 'pending'
    };
    if (await saveApplicant(applicant)) closeModal();
}

// ============================================
// Slot Alert
// ============================================
function showSlotFoundAlert() {
    const overlay = document.getElementById('slotAlertOverlay');
    const details = document.getElementById('slotDetails');
    if (slotDetails) {
        details.innerHTML = `<p><strong>Passport:</strong> ${slotDetails.passport_number || 'N/A'}</p>
            <p><strong>Date:</strong> ${slotDetails.date || 'N/A'}</p>
            <p><strong>Time:</strong> ${slotDetails.time || 'N/A'}</p>
            <p><strong>Center:</strong> ${slotDetails.center || 'N/A'}</p>
            <p><strong>Found at:</strong> ${slotDetails.found_at ? new Date(slotDetails.found_at).toLocaleString() : 'N/A'}</p>`;
    }
    overlay.classList.remove('hidden');
    overlay.classList.add('shown');
}

function dismissSlotAlert() { const o = document.getElementById('slotAlertOverlay'); o.classList.add('hidden'); o.classList.remove('shown'); }

// ============================================
// Logging
// ============================================
function addLog(message, type = '') {
    const logContainer = document.getElementById('logContainer');
    const time = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
    const entry = document.createElement('div');
    entry.className = 'log-entry' + (type ? ' ' + type : '');
    entry.innerHTML = `<span class="log-time">${time}</span><span class="log-message">${escapeHtml(message)}</span>`;
    const placeholder = logContainer.querySelector('.log-entry:only-child');
    if (placeholder && placeholder.querySelector('.log-message').textContent.includes('Waiting')) placeholder.remove();
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
    while (logContainer.children.length > 100) logContainer.firstChild.remove();
}

function clearLogs() { document.getElementById('logContainer').innerHTML = `<div class="log-entry"><span class="log-time">--:--</span><span class="log-message">Waiting for activity...</span></div>`; logCursor = 0; }

// ============================================
// Utilities
// ============================================
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    const icons = { success: '<path d="M4 8L7 11L12 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>', warning: '<path d="M8 5V8M8 11H8.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>', error: '<path d="M4 4L12 12M12 4L4 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' };
    toast.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">${icons[type] || icons.success}</svg><span>${escapeHtml(message)}</span>`;
    document.getElementById('toastContainer').appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3000);
}

function escapeHtml(text) { const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }
function generateId() { return 'app_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9); }

// Mobile validation
function isValidMobile(phone) { return /^0092\d{10}$/.test(phone); }

function validateMobileInput(e) {
    const input = e.target;
    let value = input.value.replace(/\D/g, '');
    if (value !== input.value) input.value = value;
    const errorEl = document.getElementById('mobileError');
    const hintEl = document.getElementById('mobileHint');

    if (value.length === 0) { hintEl.classList.remove('hidden'); errorEl.classList.add('hidden'); input.classList.remove('input-error-state', 'input-valid-state'); }
    else if (isValidMobile(value)) { hintEl.classList.remove('hidden'); errorEl.classList.add('hidden'); input.classList.remove('input-error-state'); input.classList.add('input-valid-state'); }
    else if (value.length < 14) { if (!value.startsWith('0092') && value.length >= 4) showMobileError(); else { hintEl.classList.remove('hidden'); errorEl.classList.add('hidden'); input.classList.remove('input-error-state'); } input.classList.remove('input-valid-state'); }
    else { showMobileError(); input.classList.remove('input-valid-state'); }
}

function showMobileError() {
    document.getElementById('mobileHint').classList.add('hidden');
    document.getElementById('mobileError').classList.remove('hidden');
    document.getElementById('mobile').classList.add('input-error-state');
}