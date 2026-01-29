const API_BASE = '';

// State
let applicants = [];
let schedule = {
    enabled: true,
    days: []
};
let selectedCenter = 'Islamabad';
let isRunning = false;

const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

// DOM Elements
const applicantsList = document.getElementById('applicantsList');
const emptyState = document.getElementById('emptyState');
const modalOverlay = document.getElementById('modalOverlay');
const applicantForm = document.getElementById('applicantForm');
const modalTitle = document.getElementById('modalTitle');
const statusIndicator = document.getElementById('statusIndicator');
const logContainer = document.getElementById('logContainer');
const toastContainer = document.getElementById('toastContainer');


document.addEventListener('DOMContentLoaded', () => {
    loadApplicants();
    loadSchedule();
    loadCenter();
    setupEventListeners();
    checkScheduledRun();
});

function setupEventListeners() {
    // Add Applicant Button
    document.getElementById('addApplicantBtn').addEventListener('click', () => {
        openModal();
    });

    // Modal Close
    document.getElementById('modalClose').addEventListener('click', closeModal);
    document.getElementById('cancelBtn').addEventListener('click', closeModal);
    modalOverlay.addEventListener('click', (e) => {
        if (e.target === modalOverlay) closeModal();
    });

    // Form Submit
    applicantForm.addEventListener('submit', handleFormSubmit);

    // Mobile number validation (real-time)
    const mobileInput = document.getElementById('mobile');
    mobileInput.addEventListener('input', validateMobileInput);
    mobileInput.addEventListener('blur', validateMobileInput);

    // Schedule - Add Day Button
    document.getElementById('addDayBtn').addEventListener('click', addScheduleDay);

    // Schedule enabled toggle (auto-save)
    document.getElementById('scheduleEnabled').addEventListener('change', (e) => {
        schedule.enabled = e.target.checked;
        saveSchedule();
    });

    // Center Selection
    document.querySelectorAll('input[name="center"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            selectedCenter = e.target.value;
            saveCenter();
        });
    });

    // Run/Stop Buttons
    document.getElementById('runBotBtn').addEventListener('click', runBot);
    document.getElementById('stopBotBtn').addEventListener('click', stopBot);

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });
}


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
        // Load from localStorage as fallback
        const stored = localStorage.getItem('applicants');
        if (stored) {
            applicants = JSON.parse(stored);
            renderApplicants();
        }
    }
}

async function saveApplicant(applicant) {
    try {
        const isNew = !applicant.id;
        const method = isNew ? 'POST' : 'PUT';
        const url = isNew
            ? `${API_BASE}/api/applicants`
            : `${API_BASE}/api/applicants/${applicant.id}`;

        const response = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(applicant)
        });

        if (response.ok) {
            const saved = await response.json();
            if (isNew) {
                applicants.push(saved);
            } else {
                const idx = applicants.findIndex(a => a.id === saved.id);
                if (idx >= 0) applicants[idx] = saved;
            }
            renderApplicants();
            showToast(isNew ? 'Applicant added' : 'Applicant updated', 'success');
            return true;
        }
    } catch (error) {
        console.error('Failed to save applicant:', error);
        // Fallback: save locally
        if (!applicant.id) {
            applicant.id = generateId();
            applicants.push(applicant);
        } else {
            const idx = applicants.findIndex(a => a.id === applicant.id);
            if (idx >= 0) applicants[idx] = applicant;
        }
        localStorage.setItem('applicants', JSON.stringify(applicants));
        renderApplicants();
        showToast('Saved locally (server unavailable)', 'success');
        return true;
    }
    return false;
}

async function deleteApplicant(id) {
    try {
        const response = await fetch(`${API_BASE}/api/applicants/${id}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            applicants = applicants.filter(a => a.id !== id);
            renderApplicants();
            showToast('Applicant removed', 'success');
            return true;
        }
    } catch (error) {
        console.error('Failed to delete applicant:', error);
        // Fallback: delete locally
        applicants = applicants.filter(a => a.id !== id);
        localStorage.setItem('applicants', JSON.stringify(applicants));
        renderApplicants();
        showToast('Removed locally', 'success');
        return true;
    }
    return false;
}

async function resetApplicant(id) {
    try {
        const response = await fetch(`${API_BASE}/api/applicants/${id}/reset`, {
            method: 'POST'
        });

        if (response.ok) {
            const updated = await response.json();
            const idx = applicants.findIndex(a => a.id === id);
            if (idx >= 0) {
                applicants[idx] = updated;
            }
            renderApplicants();
            showToast('Applicant reset to pending', 'success');
            return true;
        }
    } catch (error) {
        console.error('Failed to reset applicant:', error);
        // Fallback: reset locally
        const idx = applicants.findIndex(a => a.id === id);
        if (idx >= 0) {
            applicants[idx].status = 'pending';
            localStorage.setItem('applicants', JSON.stringify(applicants));
            renderApplicants();
            showToast('Reset locally', 'success');
            return true;
        }
    }
    return false;
}

async function loadSchedule() {
    try {
        const response = await fetch(`${API_BASE}/api/schedule`);
        if (response.ok) {
            const data = await response.json();
            // Convert old format to new if needed
            if (data.days) {
                schedule = data;
            } else if (data.start_time) {
                // Legacy format - convert
                schedule = {
                    enabled: data.enabled,
                    days: [{ day: 'Daily', slots: [{ start: data.start_time, end: data.end_time }] }]
                };
            }
        }
    } catch (error) {
        const stored = localStorage.getItem('schedule');
        if (stored) schedule = JSON.parse(stored);
    }

    document.getElementById('scheduleEnabled').checked = schedule.enabled !== false;
    renderSchedule();
}

function renderSchedule() {
    const container = document.getElementById('scheduleDays');
    const emptyState = document.getElementById('scheduleEmpty');

    container.innerHTML = '';

    if (!schedule.days || schedule.days.length === 0) {
        emptyState.classList.remove('hidden');
        return;
    }

    emptyState.classList.add('hidden');

    schedule.days.forEach((dayData, dayIndex) => {
        const dayCard = createDayCard(dayData, dayIndex);
        container.appendChild(dayCard);
    });
}

function createDayCard(dayData, dayIndex) {
    const card = document.createElement('div');
    card.className = 'day-card';
    card.dataset.dayIndex = dayIndex;

    // Day header
    const header = document.createElement('div');
    header.className = 'day-header';

    const select = document.createElement('select');
    select.className = 'day-select';
    DAYS_OF_WEEK.forEach(day => {
        const option = document.createElement('option');
        option.value = day;
        option.textContent = day;
        option.selected = day === dayData.day;
        select.appendChild(option);
    });
    select.addEventListener('change', (e) => {
        schedule.days[dayIndex].day = e.target.value;
        saveSchedule();
    });

    const actions = document.createElement('div');
    actions.className = 'day-actions';

    const addSlotBtn = document.createElement('button');
    addSlotBtn.className = 'day-btn';
    addSlotBtn.innerHTML = '+ Add Slot';
    addSlotBtn.addEventListener('click', () => addSlotToDay(dayIndex));

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'day-btn delete-day';
    deleteBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 4H11L10.2 12H3.8L3 4Z" stroke="currentColor" stroke-width="1.5"/><path d="M5 4V2.5H9V4" stroke="currentColor" stroke-width="1.5"/><path d="M2 4H12" stroke="currentColor" stroke-width="1.5"/></svg>`;
    deleteBtn.addEventListener('click', () => removeDay(dayIndex));

    actions.appendChild(addSlotBtn);
    actions.appendChild(deleteBtn);
    header.appendChild(select);
    header.appendChild(actions);

    // Time slots
    const slotsContainer = document.createElement('div');
    slotsContainer.className = 'time-slots';

    dayData.slots.forEach((slot, slotIndex) => {
        const slotEl = createSlotElement(slot, dayIndex, slotIndex);
        slotsContainer.appendChild(slotEl);
    });

    card.appendChild(header);
    card.appendChild(slotsContainer);

    return card;
}

function createSlotElement(slot, dayIndex, slotIndex) {
    const slotEl = document.createElement('div');
    slotEl.className = 'time-slot';

    const startInput = document.createElement('input');
    startInput.type = 'time';
    startInput.className = 'slot-time-input';
    startInput.value = slot.start;
    startInput.addEventListener('change', (e) => {
        schedule.days[dayIndex].slots[slotIndex].start = e.target.value;
        saveSchedule();
    });

    const separator = document.createElement('span');
    separator.className = 'slot-separator';
    separator.textContent = 'to';

    const endInput = document.createElement('input');
    endInput.type = 'time';
    endInput.className = 'slot-time-input';
    endInput.value = slot.end;
    endInput.addEventListener('change', (e) => {
        schedule.days[dayIndex].slots[slotIndex].end = e.target.value;
        saveSchedule();
    });

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'slot-delete';
    deleteBtn.innerHTML = '×';
    deleteBtn.addEventListener('click', () => removeSlot(dayIndex, slotIndex));

    slotEl.appendChild(startInput);
    slotEl.appendChild(separator);
    slotEl.appendChild(endInput);
    slotEl.appendChild(deleteBtn);

    return slotEl;
}

function addScheduleDay() {
    // Find first day not already in schedule
    const usedDays = schedule.days.map(d => d.day);
    const availableDay = DAYS_OF_WEEK.find(d => !usedDays.includes(d)) || 'Monday';

    schedule.days.push({
        day: availableDay,
        slots: [{ start: '09:00', end: '17:00' }]
    });

    renderSchedule();
    saveSchedule();
}

function removeDay(dayIndex) {
    schedule.days.splice(dayIndex, 1);
    renderSchedule();
    saveSchedule();
}

function addSlotToDay(dayIndex) {
    schedule.days[dayIndex].slots.push({ start: '09:00', end: '17:00' });
    renderSchedule();
    saveSchedule();
}

function removeSlot(dayIndex, slotIndex) {
    schedule.days[dayIndex].slots.splice(slotIndex, 1);

    // If no slots left, remove the day
    if (schedule.days[dayIndex].slots.length === 0) {
        schedule.days.splice(dayIndex, 1);
    }

    renderSchedule();
    saveSchedule();
}

// Debounce helper - wait for user to stop making changes before saving
let saveScheduleTimeout = null;

async function saveSchedule() {
    // Clear any pending save
    if (saveScheduleTimeout) {
        clearTimeout(saveScheduleTimeout);
    }

    // Wait 500ms after last change before actually saving
    saveScheduleTimeout = setTimeout(async () => {
        try {
            await fetch(`${API_BASE}/api/schedule`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(schedule)
            });
        } catch (error) {
            localStorage.setItem('schedule', JSON.stringify(schedule));
        }
    }, 500);
}

function loadCenter() {
    const stored = localStorage.getItem('selectedCenter');
    if (stored) {
        selectedCenter = stored;
        document.querySelector(`input[name="center"][value="${stored}"]`).checked = true;
    }
}

function saveCenter() {
    localStorage.setItem('selectedCenter', selectedCenter);
    addLog('Center changed to: ' + selectedCenter);
}

async function runBot() {
    if (applicants.length === 0) {
        showToast('Please add at least one applicant', 'error');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ center: selectedCenter })
        });

        if (response.ok) {
            isRunning = true;
            logCursor = 0;  // Reset log cursor for new run
            updateStatusIndicator('running');
            document.getElementById('runBotBtn').classList.add('hidden');
            document.getElementById('stopBotBtn').classList.remove('hidden');
            addLog('Bot started', 'success');
            startStatusPolling();  // Start polling to track progress
        } else {
            const error = await response.json();
            showToast(error.message || 'Failed to start bot', 'error');
        }
    } catch (error) {
        showToast('Server unavailable', 'error');
        addLog('Error: Server unavailable', 'error');
    }
}

async function stopBot() {
    try {
        await fetch(`${API_BASE}/api/stop`, { method: 'POST' });
    } catch (error) {
        // Ignore
    }

    isRunning = false;
    stopStatusPolling();
    updateStatusIndicator('idle');
    document.getElementById('runBotBtn').classList.remove('hidden');
    document.getElementById('stopBotBtn').classList.add('hidden');
    addLog('Bot stopped');
    loadApplicants();  // Refresh list
}

// ============================================
// Rendering
// ============================================

function renderApplicants() {
    if (applicants.length === 0) {
        emptyState.classList.remove('hidden');
        applicantsList.querySelectorAll('.applicant-card').forEach(el => el.remove());
        return;
    }

    emptyState.classList.add('hidden');

    // Clear existing cards
    applicantsList.querySelectorAll('.applicant-card').forEach(el => el.remove());

    // Render each applicant
    applicants.forEach((applicant, index) => {
        const card = createApplicantCard(applicant, index);
        applicantsList.appendChild(card);
    });
}

function createApplicantCard(applicant, index) {
    const card = document.createElement('div');
    card.className = 'applicant-card';
    if (applicant.status === 'processing') card.classList.add('processing');
    if (applicant.status === 'completed') card.classList.add('completed');
    if (applicant.status === 'failed') card.classList.add('failed');

    // Show status badge for non-pending
    const statusBadge = applicant.status !== 'pending'
        ? `<span class="status-badge ${applicant.status}">${applicant.status}</span>`
        : '';

    card.innerHTML = `
        <div class="applicant-number">${index + 1}</div>
        <div class="applicant-info">
            <div class="applicant-passport">${escapeHtml(applicant.passport_number)} ${statusBadge}</div>
            <div class="applicant-details">
                <span>Visa: ${escapeHtml(applicant.visa_number)}</span>
                <span>${escapeHtml(applicant.email)}</span>
            </div>
        </div>
        <div class="applicant-actions">
            ${applicant.status !== 'pending' ? `
            <button class="action-btn reset" title="Reset to Pending" data-id="${applicant.id}">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M2 8C2 4.68629 4.68629 2 8 2C10.5 2 12.5 3.5 13.5 5.5M14 8C14 11.3137 11.3137 14 8 14C5.5 14 3.5 12.5 2.5 10.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                    <path d="M13 2V6H9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M3 14V10H7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </button>
            ` : ''}
            <button class="action-btn edit" title="Edit" data-id="${applicant.id}">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M11.5 2.5L13.5 4.5L5 13H3V11L11.5 2.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
                </svg>
            </button>
            <button class="action-btn delete" title="Delete" data-id="${applicant.id}">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M4 5H12L11.2 14H4.8L4 5Z" stroke="currentColor" stroke-width="1.5"/>
                    <path d="M6 5V3H10V5" stroke="currentColor" stroke-width="1.5"/>
                    <path d="M2 5H14" stroke="currentColor" stroke-width="1.5"/>
                </svg>
            </button>
        </div>
    `;

    // Reset handler
    const resetBtn = card.querySelector('.reset');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => resetApplicant(applicant.id));
    }

    // Edit handler
    card.querySelector('.edit').addEventListener('click', () => {
        openModal(applicant);
    });

    // Delete handler
    card.querySelector('.delete').addEventListener('click', () => {
        if (confirm('Remove this applicant?')) {
            deleteApplicant(applicant.id);
        }
    });

    return card;
}


function openModal(applicant = null) {
    modalTitle.textContent = applicant ? 'Edit Applicant' : 'Add Applicant';

    if (applicant) {
        document.getElementById('applicantId').value = applicant.id;
        document.getElementById('passportNumber').value = applicant.passport_number;
        document.getElementById('visaNumber').value = applicant.visa_number;
        document.getElementById('mobile').value = formatPhoneForDisplay(applicant.mobile);
        document.getElementById('email').value = applicant.email;
    } else {
        applicantForm.reset();
        document.getElementById('applicantId').value = '';
    }

    modalOverlay.classList.remove('hidden');
    document.getElementById('passportNumber').focus();
}

function closeModal() {
    modalOverlay.classList.add('hidden');
    applicantForm.reset();
}

async function handleFormSubmit(e) {
    e.preventDefault();

    // Validate mobile before submit
    const mobileValue = document.getElementById('mobile').value;
    if (!isValidMobile(mobileValue)) {
        showMobileError();
        document.getElementById('mobile').focus();
        return;
    }

    const id = document.getElementById('applicantId').value;
    const applicant = {
        id: id || null,
        country: 'Pakistan',
        passport_number: document.getElementById('passportNumber').value.toUpperCase().trim(),
        visa_number: document.getElementById('visaNumber').value.toUpperCase().trim(),
        mobile: mobileValue,  // Already validated as 14 digits
        email: document.getElementById('email').value.toLowerCase().trim(),
        status: 'pending'
    };

    const success = await saveApplicant(applicant);
    if (success) {
        closeModal();
    }
}


function updateStatusIndicator(status) {
    statusIndicator.className = 'status-indicator';
    const statusText = statusIndicator.querySelector('.status-text');

    switch (status) {
        case 'running':
            statusIndicator.classList.add('running');
            statusText.textContent = 'Running';
            break;
        case 'scheduled':
            statusIndicator.classList.add('scheduled');
            statusText.textContent = 'Scheduled';
            break;
        default:
            statusText.textContent = 'Idle';
    }
}

function addLog(message, type = '') {
    const now = new Date();
    const time = now.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    });

    const entry = document.createElement('div');
    entry.className = 'log-entry' + (type ? ' ' + type : '');
    entry.innerHTML = `
        <span class="log-time">${time}</span>
        <span class="log-message">${escapeHtml(message)}</span>
    `;

    // Remove placeholder if exists
    const placeholder = logContainer.querySelector('.log-entry:only-child');
    if (placeholder && placeholder.querySelector('.log-message').textContent.includes('Waiting')) {
        placeholder.remove();
    }

    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;

    // Keep only last 50 entries
    while (logContainer.children.length > 50) {
        logContainer.firstChild.remove();
    }
}

let pollingInterval = null;
let logCursor = 0;  // Track which logs we've already received

function startStatusPolling() {
    // Only poll when bot is running
    if (pollingInterval) return;  // Already polling

    pollingInterval = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE}/api/status?log_cursor=${logCursor}`);
            if (response.ok) {
                const status = await response.json();
                isRunning = status.running;

                // Update cursor for next poll
                if (status.log_cursor !== undefined) {
                    logCursor = status.log_cursor;
                }

                if (status.running) {
                    updateStatusIndicator('running');
                    document.getElementById('runBotBtn').classList.add('hidden');
                    document.getElementById('stopBotBtn').classList.remove('hidden');
                } else {
                    // Bot stopped - update UI and stop polling
                    if (schedule.enabled && isInScheduledWindow()) {
                        updateStatusIndicator('scheduled');
                    } else {
                        updateStatusIndicator('idle');
                    }
                    document.getElementById('runBotBtn').classList.remove('hidden');
                    document.getElementById('stopBotBtn').classList.add('hidden');

                    // Refresh applicants list and stop polling
                    loadApplicants();
                    stopStatusPolling();
                }

                // Update applicant statuses while running
                if (status.applicants) {
                    status.applicants.forEach(update => {
                        const idx = applicants.findIndex(a => a.id === update.id);
                        if (idx >= 0) {
                            applicants[idx].status = update.status;
                        }
                    });
                    renderApplicants();
                }

                // Add only NEW log messages (cursor-based)
                if (status.logs && status.logs.length > 0) {
                    status.logs.forEach(log => addLog(log.message, log.type));
                }
            }
        } catch (error) {
            // Server unavailable, stop polling
            stopStatusPolling();
        }
    }, 3000);
}

function stopStatusPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
}

function checkScheduledRun() {
    setInterval(() => {
        if (schedule.enabled && isInScheduledWindow() && !isRunning && applicants.length > 0) {
            // Check if we should auto-start
            const lastRun = localStorage.getItem('lastScheduledRun');
            const today = new Date().toDateString();

            if (lastRun !== today) {
                localStorage.setItem('lastScheduledRun', today);
                addLog('Auto-starting scheduled run...');
                runBot();
            }
        }
    }, 60000); // Check every minute
}

function isInScheduledWindow() {
    if (!schedule.days || schedule.days.length === 0) return false;

    const now = new Date();
    const currentTime = now.getHours() * 60 + now.getMinutes();
    const currentDayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][now.getDay()];

    // Check each day in schedule
    for (const dayData of schedule.days) {
        // Check if this day matches (or is 'Daily')
        if (dayData.day !== currentDayName && dayData.day !== 'Daily') continue;

        // Check each slot
        for (const slot of dayData.slots) {
            const [startH, startM] = slot.start.split(':').map(Number);
            const [endH, endM] = slot.end.split(':').map(Number);

            const startMinutes = startH * 60 + startM;
            const endMinutes = endH * 60 + endM;

            // Handle overnight schedules
            if (endMinutes < startMinutes) {
                if (currentTime >= startMinutes || currentTime < endMinutes) return true;
            } else {
                if (currentTime >= startMinutes && currentTime < endMinutes) return true;
            }
        }
    }

    return false;
}


function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            ${type === 'success'
            ? '<path d="M4 8L7 11L12 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
            : '<path d="M4 4L12 12M12 4L4 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
        }
        </svg>
        <span>${escapeHtml(message)}</span>
    `;

    toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function generateId() {
    return 'app_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}


function isValidMobile(phone) {
    // Must be exactly 14 digits starting with 0092
    const pattern = /^0092\d{10}$/;
    return pattern.test(phone);
}

function validateMobileInput(e) {
    const input = e.target;
    const value = input.value;
    const errorEl = document.getElementById('mobileError');
    const hintEl = document.getElementById('mobileHint');

    // Remove any non-digit characters as user types
    const digitsOnly = value.replace(/\D/g, '');
    if (digitsOnly !== value) {
        input.value = digitsOnly;
    }
    z
    // Validate
    if (value.length === 0) {
        // Empty - show hint
        hintEl.classList.remove('hidden');
        errorEl.classList.add('hidden');
        input.classList.remove('input-error-state');
    } else if (isValidMobile(value)) {
        // Valid
        hintEl.classList.remove('hidden');
        errorEl.classList.add('hidden');
        input.classList.remove('input-error-state');
        input.classList.add('input-valid-state');
    } else if (value.length < 14) {
        // Still typing
        if (!value.startsWith('0092') && value.length >= 4) {
            showMobileError();
        } else {
            hintEl.classList.remove('hidden');
            errorEl.classList.add('hidden');
            input.classList.remove('input-error-state');
        }
        input.classList.remove('input-valid-state');
    } else {
        // Wrong format
        showMobileError();
        input.classList.remove('input-valid-state');
    }
}


function showMobileError() {
    const errorEl = document.getElementById('mobileError');
    const hintEl = document.getElementById('mobileHint');
    const input = document.getElementById('mobile');

    hintEl.classList.add('hidden');
    errorEl.classList.remove('hidden');
    input.classList.add('input-error-state');
}

function formatPhoneForDisplay(phone) {
    // Already in correct format, just return as-is
    if (!phone) return '';
    return phone;
}
