const tokenKey = 'los-lagos-access-token';
let token = localStorage.getItem(tokenKey);
let adminUser = null;
let incidents = [];
let users = [];

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value = '') {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&#39;'
  })[character]);
}

function showMessage(message, isError = false) {
  $('#adminMessage').textContent = message;
  $('#adminMessage').classList.toggle('error', isError);
  window.clearTimeout(showMessage.timeout);
  showMessage.timeout = window.setTimeout(() => { $('#adminMessage').textContent = ''; }, 6000);
}

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (options.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(url, { ...options, headers });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(data?.message || `Error ${response.status}`);
  return data;
}

function formatDate(value) {
  if (!value) return 'Sin fecha';
  return new Intl.DateTimeFormat('es-CL', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}

function veracityClass(veracity) {
  return {
    sin_verificar: 'veracity-unverified',
    confirmado_comunidad: 'veracity-community',
    verificado: 'veracity-verified',
    probablemente_falso: 'veracity-doubtful',
    falso: 'veracity-false'
  }[veracity] || 'veracity-unverified';
}

async function loadData() {
  const [freshIncidents, freshUsers, stats] = await Promise.all([
    api('/api/admin/incidents'),
    api('/api/users'),
    api('/api/admin/stats')
  ]);
  incidents = freshIncidents;
  users = freshUsers;
  renderStats(stats);
  renderUsers();
  renderIncidents();
}

function renderStats(stats) {
  const items = [
    ['Total', stats.total], ['Abiertos', stats.open], ['En proceso', stats.inProgress],
    ['Resueltos', stats.resolved], ['Alta prioridad', stats.highPriority],
    ['Ocultos', stats.hidden]
  ];
  $('#adminStats').innerHTML = items.map(([label, value]) =>
    `<article class="admin-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`
  ).join('');
}

function filteredIncidents() {
  const query = $('#adminSearch').value.trim().toLocaleLowerCase('es');
  const status = $('#adminStatusFilter').value;
  const veracity = $('#adminVeracityFilter').value;
  return incidents.filter((incident) => {
    const textMatch = !query || `${incident.type} ${incident.description}`.toLocaleLowerCase('es').includes(query);
    const statusMatch = status === 'todos' || incident.status === status;
    const veracityMatch = veracity === 'todos'
      || (veracity === 'ocultas' ? incident.hidden : incident.veracity === veracity);
    return textMatch && statusMatch && veracityMatch;
  });
}

function operatorOptions(incident) {
  return users
    .filter((person) => ['operador', 'administrador'].includes(person.role))
    .map((person) => `<option value="${escapeHtml(person.id)}" ${incident.assignedTo?.id === person.id ? 'selected' : ''}>${escapeHtml(person.name)}</option>`)
    .join('');
}

function renderIncidents() {
  const filtered = filteredIncidents();
  $('#adminIncidentCount').textContent = String(filtered.length);
  if (!filtered.length) {
    $('#adminIncidentList').innerHTML = '<p class="empty-state">No hay incidencias que coincidan.</p>';
    return;
  }
  $('#adminIncidentList').innerHTML = filtered.map((incident) => {
    const automatic = ['confirmado_comunidad', 'probablemente_falso'].includes(incident.veracity);
    return `<article class="admin-incident-card${incident.hidden ? ' hidden-incident' : ''}" data-id="${escapeHtml(incident.id)}">
      <div class="admin-card-head">
        <div><h3>${escapeHtml(incident.type)}</h3><p>${escapeHtml(incident.description)}</p></div>
        <span class="veracity-chip ${veracityClass(incident.veracity)}">${escapeHtml(incident.veracityLabel)}</span>
      </div>
      <div class="admin-controls">
        <label>Estado<select class="admin-status" data-id="${escapeHtml(incident.id)}">
          ${['abierto', 'en proceso', 'resuelto'].map((value) => `<option value="${value}" ${incident.status === value ? 'selected' : ''}>${value}</option>`).join('')}
        </select></label>
        <label>Prioridad<select class="admin-priority" data-id="${escapeHtml(incident.id)}">
          ${['baja', 'media', 'alta', 'crítica'].map((value) => `<option value="${value}" ${incident.priority === value ? 'selected' : ''}>${value}</option>`).join('')}
        </select></label>
        <label>Veracidad<select class="admin-veracity" data-id="${escapeHtml(incident.id)}" ${automatic ? 'disabled' : ''}>
          <option value="sin_verificar" ${incident.veracity === 'sin_verificar' ? 'selected' : ''}>Sin verificar</option>
          <option value="verificado" ${incident.veracity === 'verificado' ? 'selected' : ''}>Verificada</option>
          <option value="falso" ${incident.veracity === 'falso' ? 'selected' : ''}>Falsa</option>
        </select></label>
        <label>Asignar<select class="admin-assignee" data-id="${escapeHtml(incident.id)}">
          <option value="">Sin asignar</option>${operatorOptions(incident)}
        </select></label>
        <label style="grid-column: span 2">Comentario<input class="admin-comment" data-id="${escapeHtml(incident.id)}" maxlength="1000" placeholder="Comentario visible para todos" /></label>
      </div>
      <div class="admin-actions">
        <button type="button" class="secondary-button admin-comment-btn" data-id="${escapeHtml(incident.id)}">Comentar</button>
        <button type="button" class="secondary-button admin-renew" data-id="${escapeHtml(incident.id)}">Renovar vigencia</button>
        <button type="button" class="secondary-button admin-hidden" data-id="${escapeHtml(incident.id)}">${incident.hidden ? 'Restaurar' : 'Ocultar'}</button>
        <button type="button" class="secondary-button admin-delete" data-id="${escapeHtml(incident.id)}">Eliminar</button>
      </div>
      <small>${escapeHtml(formatDate(incident.time))}${incident.community ? ` · ${escapeHtml(incident.community.name)}` : ''}</small>
    </article>`;
  }).join('');
}

function renderUsers() {
  $('#adminUserList').innerHTML = users.map((person) => `<article class="admin-user">
    <div><strong>${escapeHtml(person.name)}</strong><span>${escapeHtml(person.email)} · ${escapeHtml(person.reputation)} reputación</span></div>
    <select class="admin-user-role" data-id="${escapeHtml(person.id)}" ${person.id === adminUser.id ? 'disabled' : ''}>
      <option value="ciudadano" ${person.role === 'ciudadano' ? 'selected' : ''}>Ciudadano</option>
      <option value="operador" ${person.role === 'operador' ? 'selected' : ''}>Operador</option>
      <option value="administrador" ${person.role === 'administrador' ? 'selected' : ''}>Administrador</option>
    </select>
  </article>`).join('');
}

async function updateIncident(id, body) {
  await api(`/api/incidents/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) });
  await loadData();
  showMessage('Incidencia actualizada.');
}

document.addEventListener('change', async (event) => {
  const target = event.target;
  try {
    if (target.matches('.admin-status')) await updateIncident(target.dataset.id, { status: target.value });
    if (target.matches('.admin-priority')) await updateIncident(target.dataset.id, { priority: target.value });
    if (target.matches('.admin-veracity')) await updateIncident(target.dataset.id, { veracity: target.value });
    if (target.matches('.admin-assignee')) await updateIncident(target.dataset.id, { assignedTo: target.value || null });
    if (target.matches('.admin-user-role')) {
      await api(`/api/users/${encodeURIComponent(target.dataset.id)}/role`, {
        method: 'PUT',
        body: JSON.stringify({ role: target.value })
      });
      await loadData();
      showMessage('Rol actualizado.');
    }
  } catch (error) {
    showMessage(error.message, true);
    await loadData();
  }
});

document.addEventListener('click', async (event) => {
  const button = event.target.closest('.admin-actions button');
  if (!button) return;
  const id = button.dataset.id;
  try {
    if (button.matches('.admin-comment-btn')) {
      const input = document.querySelector(`.admin-comment[data-id="${CSS.escape(id)}"]`);
      if (!input.value.trim()) throw new Error('Escribe un comentario.');
      await updateIncident(id, { comment: input.value });
    } else if (button.matches('.admin-renew')) {
      await updateIncident(id, { renew: true });
    } else if (button.matches('.admin-hidden')) {
      const incident = incidents.find((item) => item.id === id);
      await updateIncident(id, { hidden: !incident.hidden });
    } else if (button.matches('.admin-delete')) {
      if (!window.confirm('¿Eliminar esta incidencia? Esta acción no se puede deshacer.')) return;
      await api(`/api/incidents/${encodeURIComponent(id)}`, { method: 'DELETE' });
      await loadData();
      showMessage('Incidencia eliminada.');
    }
  } catch (error) { showMessage(error.message, true); }
});

['#adminSearch', '#adminStatusFilter', '#adminVeracityFilter'].forEach((selector) =>
  $(selector).addEventListener(selector === '#adminSearch' ? 'input' : 'change', renderIncidents)
);
$('#refreshAdminBtn').addEventListener('click', () => loadData().catch((error) => showMessage(error.message, true)));
$('#adminLogoutBtn').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch {}
  localStorage.removeItem(tokenKey);
  window.location.href = '/';
});

async function initialize() {
  if (!token) {
    $('#accessDenied').classList.remove('hidden');
    $('#adminUserLabel').textContent = 'Sin sesión';
    return;
  }
  try {
    const result = await api('/api/auth/me');
    adminUser = result.user;
    if (adminUser.role !== 'administrador') {
      $('#accessDenied').classList.remove('hidden');
      $('#adminUserLabel').textContent = `${adminUser.name} · ${adminUser.role}`;
      return;
    }
    $('#adminUserLabel').textContent = `${adminUser.name} · Administrador`;
    $('#adminContent').classList.remove('hidden');
    await loadData();
  } catch (error) {
    $('#accessDenied').classList.remove('hidden');
    $('#adminUserLabel').textContent = 'Sesión no válida';
  }
}

initialize();
