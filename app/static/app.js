const state = { groups: [], users: [], group: null, members: [], expenses: [], view: 'overview' };
const $ = (selector) => document.querySelector(selector);
const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 });
let toastTimer;
let workspaceSyncRunning = false;
let workspaceSyncQueued = false;
let workspaceSyncWaiters = [];

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  // GET requests do not carry a JSON body; setting Content-Type on them can
  // trigger unnecessary middleware handling in some local browser setups.
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    const detail = Array.isArray(data?.detail)
      ? data.detail.map((item) => item.msg).join(', ')
      : (data?.detail || `Request failed (${response.status})`);
    throw new Error(`${path}: ${detail}`);
  }
  return data;
}

function showToast(message, kind = 'success') {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.toggle('error', kind === 'error');
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}

function setSyncStatus(connected) {
  const status = $('#syncState');
  status.classList.toggle('offline', !connected);
  $('#syncLabel').textContent = connected ? 'Synced with backend' : 'Backend unavailable';
}

function safe(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function userLabel(user) {
  const duplicates = state.users.filter((item) => item.name.toLowerCase() === user.name.toLowerCase());
  if (duplicates.length < 2) return user.name;
  const account = duplicates.findIndex((item) => item.id === user.id) + 1;
  return `${user.name} · profile ${account}`;
}

function groupLabel(group) {
  const matches = state.groups.filter((item) => item.name.trim().toLowerCase() === group.name.trim().toLowerCase());
  return matches.length > 1 ? `${group.name} (#${group.id})` : group.name;
}

function initials(name) {
  return (name || '?').trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
}

function formatDate(value) {
  if (!value) return 'Just now';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short' }).format(date);
}

function activeNav(view) {
  state.view = view;
  document.querySelectorAll('.nav-link').forEach((link) => link.classList.toggle('active', link.dataset.view === view));
  if (view === 'expenses') $('#expensesPanel').scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (view === 'people') $('#peoplePanel').scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (view === 'overview') window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderGroups() {
  const list = $('#groupList');
  list.replaceChildren();
  state.groups.forEach((group) => {
    const button = document.createElement('button');
    button.className = `group-link${state.group?.id === group.id ? ' selected' : ''}`;
    button.innerHTML = `<span class="group-dot"></span><span class="group-name">${safe(groupLabel(group))}</span>`;
    button.title = groupLabel(group);
    button.addEventListener('click', () => selectGroup(group.id));
    list.append(button);
  });
  const mobileSelect = $('#mobileGroupSelect');
  mobileSelect.replaceChildren();
  state.groups.forEach((group) => {
    const option = document.createElement('option');
    option.value = group.id;
    option.textContent = groupLabel(group);
    option.selected = state.group?.id === group.id;
    mobileSelect.append(option);
  });
  mobileSelect.disabled = state.groups.length === 0;
}

function renderEmpty() {
  const hasGroup = Boolean(state.group);
  $('#emptyState').hidden = hasGroup;
  $('#dashboard').hidden = !hasGroup;
  $('#addExpenseButton').disabled = !hasGroup;
  $('#deleteGroupButton').disabled = !hasGroup;
  $('#crumbGroup').textContent = state.group?.name || 'Your groups';
  if (state.group) document.title = `${state.group.name} — Expense Split`;
}

function renderMembers() {
  const list = $('#memberList');
  list.replaceChildren();
  $('#noMembers').hidden = state.members.length > 0;
  state.members.forEach((member) => {
    const row = document.createElement('div');
    row.className = 'member-row';
    row.innerHTML = `<span class="avatar">${safe(initials(member.name))}</span><span class="member-details"><span class="member-name">${safe(member.name)}</span><span class="member-note">In this group</span></span>`;
    const removeButton = document.createElement('button');
    removeButton.className = 'member-remove';
    removeButton.type = 'button';
    removeButton.textContent = 'Remove';
    removeButton.setAttribute('aria-label', `Remove ${member.name} from this group`);
    removeButton.addEventListener('click', () => removeMemberFromGroup(member));
    row.append(removeButton);
    list.append(row);
  });
  $('#splitHint').textContent = state.members.length
    ? `Split equally between ${state.members.length} ${state.members.length === 1 ? 'person' : 'people'} in this group.`
    : 'Add people to this group before recording an expense.';
  $('#addExpenseButton').disabled = !state.group || state.members.length === 0;
  renderMemberOptions();
  renderCustomSplitInputs();
}

function renderCustomSplitInputs() {
  const container = $('#customSplitFields');
  container.replaceChildren();
  state.members.forEach((member) => {
    const row = document.createElement('label');
    row.className = 'custom-split-row';
    const name = document.createElement('span');
    name.textContent = member.name;
    const input = document.createElement('input');
    input.type = 'number';
    input.name = `split_${member.id}`;
    input.min = '0';
    input.step = '0.01';
    input.placeholder = '0.00';
    row.append(name, input);
    container.append(row);
  });
  updateSplitModeUI();
}

function updateSplitModeUI() {
  const custom = $('#splitMode').value === 'custom';
  const container = $('#customSplitFields');
  container.hidden = !custom;
  container.querySelectorAll('input').forEach((input) => { input.required = custom; });
  $('#splitHint').textContent = !state.members.length
    ? 'Add people to this group before recording an expense.'
    : (custom
      ? 'Enter each person’s share; amounts must add up to the expense total. Use ₹0 for someone who is not sharing.'
      : `Split equally between ${state.members.length} ${state.members.length === 1 ? 'person' : 'people'} in this group.`);
}

async function removeMemberFromGroup(member) {
  const group = state.group;
  if (!group) return;
  const isLastMemberWithExpenses = state.members.length === 1 && state.expenses.length > 0;
  const message = isLastMemberWithExpenses
    ? `Remove ${member.name} from “${group.name}”? This is the last member. Equal expense shares will clear until someone joins again; custom shares will stay with their recorded participants. Their profile will stay saved.`
    : `Remove ${member.name} from “${group.name}”? Existing expenses will be split among the remaining members. Their profile will stay saved.`;
  const confirmed = window.confirm(message);
  if (!confirmed) return;
  try {
    await api(`/groups/${group.id}/members/${member.id}`, { method: 'DELETE' });
    const synced = await refreshGroup();
    showToast(synced ? `${member.name} was removed from the group.` : 'Member removed. Refresh the dashboard to sync.', synced ? 'success' : 'error');
  } catch (error) {
    showToast(error.message, 'error');
  }
}

function renderMemberOptions() {
  const payerSelect = $('#payerSelect');
  const available = $('#availableUserSelect');
  payerSelect.innerHTML = '';
  state.members.forEach((member) => {
    const option = document.createElement('option');
    option.value = member.id;
    option.textContent = userLabel(member);
    payerSelect.append(option);
  });
  const members = new Set(state.members.map((member) => member.id));
  const choices = state.users.filter((user) => !members.has(user.id));
  available.innerHTML = '<option value="">Choose an existing person</option>';
  choices.forEach((user) => {
    const option = document.createElement('option');
    option.value = user.id;
    option.textContent = userLabel(user);
    available.append(option);
  });
  available.disabled = choices.length === 0;
}

function renderExpenses() {
  const list = $('#expenseList');
  list.replaceChildren();
  $('#noExpenses').hidden = state.expenses.length > 0;
  $('#expenseCount').textContent = state.expenses.length;
  const total = state.expenses.reduce((sum, expense) => sum + Number(expense.amount), 0);
  $('#totalSpent').textContent = money.format(total);
  state.expenses.slice(0, 8).forEach((expense, index) => {
    const row = document.createElement('div');
    row.className = 'expense-row';
    const symbols = ['↗', '⌁', '✳', '◌'];
    const splitSummary = expense.split_type === 'custom'
      ? `<span class="expense-split">Custom: ${(expense.splits || []).filter((share) => Number(share.amount) > 0).map((share) => `${safe(share.name)} ${safe(money.format(Number(share.amount)))}`).join(' · ')}</span>`
      : '';
    row.innerHTML = `<span class="expense-symbol">${symbols[index % symbols.length]}</span>
      <span><span class="expense-name">${safe(expense.description)}</span><span class="expense-meta">Paid by ${safe(expense.paid_by?.name || 'Unknown')} · ${safe(formatDate(expense.created_at))}</span>${splitSummary}</span>
      <span class="expense-amount">${money.format(Number(expense.amount))}</span>`;
    list.append(row);
  });
}

function renderBalances(balances, suggestions) {
  const outstanding = balances.balances.reduce((sum, person) => sum + Math.max(0, Number(person.net)), 0);
  $('#openBalance').textContent = money.format(outstanding);
  $('#settlementList').replaceChildren();
  $('#transferCount').textContent = `${suggestions.transfer_count} suggested ${suggestions.transfer_count === 1 ? 'transfer' : 'transfers'}`;
  $('#noSettlements').hidden = suggestions.suggestions.length > 0;
  suggestions.suggestions.forEach((transfer) => {
    const row = document.createElement('div');
    row.className = 'settlement-row';
    row.innerHTML = `<span class="transfer-people"><b>${safe(transfer.from)}</b> pays <b>${safe(transfer.to)}</b></span><span class="transfer-amount">${money.format(Number(transfer.amount))}</span>`;
    $('#settlementList').append(row);
  });
}

async function refreshGroup({ quiet = false } = {}) {
  if (workspaceSyncRunning) {
    workspaceSyncQueued = true;
    return new Promise((resolve) => workspaceSyncWaiters.push(resolve));
  }
  workspaceSyncRunning = true;
  $('#syncLabel').textContent = 'Syncing…';
  const requestedGroupId = state.group?.id ?? null;
  let synced = false;
  try {
    const path = requestedGroupId
      ? `/workspace?group_id=${encodeURIComponent(requestedGroupId)}`
      : '/workspace';
    const snapshot = await api(path);

    // A group can be changed while a request is in flight. Discard that older
    // response and immediately fetch the newly selected group.
    if (requestedGroupId !== (state.group?.id ?? null)) {
      workspaceSyncQueued = true;
      return false;
    }

    state.groups = snapshot.groups;
    state.users = snapshot.users;
    if (!requestedGroupId) {
      const savedId = Number(localStorage.getItem('tally-group-id'));
      state.group = state.groups.find((group) => group.id === savedId) || state.groups[0] || null;
      if (state.group) {
        localStorage.setItem('tally-group-id', state.group.id);
        workspaceSyncQueued = true;
      }
    } else {
      state.group = snapshot.dashboard?.group || state.groups.find((group) => group.id === requestedGroupId) || null;
    }

    renderEmpty();
    renderGroups();
    if (!snapshot.dashboard) {
      state.members = [];
      state.expenses = [];
      renderMembers();
      renderExpenses();
      $('#openBalance').textContent = money.format(0);
      $('#settlementList').replaceChildren();
      $('#transferCount').textContent = '0 suggested transfers';
      $('#noSettlements').hidden = false;
    } else {
      state.members = snapshot.dashboard.members;
      state.expenses = snapshot.dashboard.expenses;
      renderMembers();
      renderExpenses();
      renderBalances(snapshot.dashboard.balances, snapshot.dashboard.settlement_suggestions);
    }
    synced = true;
    setSyncStatus(true);
  } catch (error) {
    setSyncStatus(false);
    if (!quiet) showToast(`Could not sync with the backend: ${error.message}`, 'error');
  } finally {
    workspaceSyncRunning = false;
    if (workspaceSyncQueued) {
      workspaceSyncQueued = false;
      const waiters = workspaceSyncWaiters.splice(0);
      const refreshed = await refreshGroup({ quiet: true });
      waiters.forEach((resolve) => resolve(refreshed));
    }
  }
  return synced;
}

async function selectGroup(id) {
  state.group = state.groups.find((group) => group.id === Number(id)) || null;
  renderGroups();
  if (state.group) localStorage.setItem('tally-group-id', state.group.id);
  await refreshGroup();
}

async function loadApp() {
  const synced = await refreshGroup();
  if (!synced) showToast('Could not load the workspace. Check that the API server is running, then refresh.', 'error');
}

// Refresh edits made through Swagger or another open tab when this page is
// revisited, and periodically while it stays visible.
window.addEventListener('focus', () => void refreshGroup({ quiet: true }));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') void refreshGroup({ quiet: true });
});
window.setInterval(() => {
  if (document.visibilityState === 'visible' && !document.querySelector('dialog[open]')) {
    void refreshGroup({ quiet: true });
  }
}, 15000);

function openDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog && !dialog.open) dialog.showModal();
}

function closeDialog(id) {
  document.getElementById(id)?.close();
}

$('#newGroupButton').addEventListener('click', () => openDialog('groupDialog'));
$('#mobileNewGroupButton').addEventListener('click', () => openDialog('groupDialog'));
$('#emptyCreateGroup').addEventListener('click', () => openDialog('groupDialog'));
$('#mobileGroupSelect').addEventListener('change', (event) => selectGroup(event.currentTarget.value));
$('#deleteGroupButton').addEventListener('click', async () => {
  const group = state.group;
  if (!group) return;
  const confirmed = window.confirm(
    `Permanently delete “${group.name}” and all its expenses and group memberships? This cannot be undone. User profiles will remain.`
  );
  if (!confirmed) return;
  const button = $('#deleteGroupButton');
  button.disabled = true;
  try {
    await api(`/groups/${group.id}`, { method: 'DELETE' });
    state.groups = state.groups.filter((item) => item.id !== group.id);
    state.group = state.groups[0] || null;
    if (state.group) localStorage.setItem('tally-group-id', state.group.id);
    else localStorage.removeItem('tally-group-id');
    renderEmpty();
    renderGroups();
    const synced = await refreshGroup();
    showToast(synced ? `“${group.name}” was permanently deleted.` : 'Group deleted. Refresh the dashboard to sync.', synced ? 'success' : 'error');
  } catch (error) {
    showToast(error.message, 'error');
    button.disabled = false;
  }
});
$('#addExpenseButton').addEventListener('click', () => {
  if (!state.group) return showToast('Create or choose a group first.', 'error');
  if (!state.members.length) return showToast('Add group members before recording an expense.', 'error');
  $('#expenseForm').reset();
  renderCustomSplitInputs();
  openDialog('expenseDialog');
});
$('#splitMode').addEventListener('change', updateSplitModeUI);
$('#addMemberButton').addEventListener('click', () => {
  if (!state.group) return showToast('Create a group first.', 'error');
  renderMemberOptions();
  openDialog('memberDialog');
});
document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => closeDialog(button.dataset.close)));
document.querySelectorAll('.nav-link').forEach((link) => link.addEventListener('click', (event) => {
  event.preventDefault();
  activeNav(link.dataset.view);
}));
$('#seeExpenses').addEventListener('click', () => activeNav('expenses'));

$('#groupForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const name = new FormData(form).get('name');
  try {
    const group = await api('/groups', { method: 'POST', body: JSON.stringify({ name }) });
    state.groups.unshift(group);
    state.group = group;
    localStorage.setItem('tally-group-id', group.id);
    form.reset();
    closeDialog('groupDialog');
    const synced = await refreshGroup();
    showToast(synced ? `${group.name} is ready. Add your people next.` : 'Group saved. Refresh the dashboard to see it.', synced ? 'success' : 'error');
  } catch (error) { showToast(error.message, 'error'); }
});

$('#memberForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!state.group) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const newName = String(formData.get('new_name') || '').trim();
  try {
    let userId = Number(formData.get('user_id'));
    if (newName) {
      const created = await api('/users', { method: 'POST', body: JSON.stringify({ name: newName }) });
      state.users.push(created);
      userId = created.id;
    }
    if (!userId) return showToast('Choose someone or enter a new person’s name.', 'error');
    await api(`/groups/${state.group.id}/members`, { method: 'POST', body: JSON.stringify({ user_id: userId }) });
    form.reset();
    closeDialog('memberDialog');
    const synced = await refreshGroup();
    showToast(synced ? 'Member added. Existing expenses are now split across everyone.' : 'Person added. Refresh the dashboard to see the update.', synced ? 'success' : 'error');
  } catch (error) { showToast(error.message, 'error'); }
});

$('#expenseForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!state.group) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const body = {
    paid_by: Number(formData.get('paid_by')),
    amount: String(formData.get('amount')),
    description: String(formData.get('description')).trim(),
  };
  if (formData.get('split_mode') === 'custom') {
    body.splits = state.members.map((member) => ({
      user_id: member.id,
      amount: String(formData.get(`split_${member.id}`) ?? '').trim(),
    }));
  }
  try {
    await api(`/groups/${state.group.id}/expenses`, { method: 'POST', body: JSON.stringify(body) });
    form.reset();
    renderCustomSplitInputs();
    closeDialog('expenseDialog');
    const synced = await refreshGroup();
    showToast(synced ? 'Expense added. The numbers are up to date.' : 'Expense saved. Refresh the dashboard to see the update.', synced ? 'success' : 'error');
  } catch (error) { showToast(error.message, 'error'); }
});

loadApp();
