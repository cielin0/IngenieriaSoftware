const map = window.appMap;
const incidentsLayer = window.incidentsLayer;
const geoLayers = window.geoLayers;
const $ = (selector) => document.querySelector(selector);
const tokenKey = 'los-lagos-access-token';
const communityKey = 'los-lagos-community';
const DEFAULT_COMMUNITY = 'los-lagos';
let token = localStorage.getItem(tokenKey);
let user = null;
let incidents = [];
let operators = [];
let meta = { types: [], veracity: [], flagReasons: [], communities: [], chile: { polygon: [], islands: [] } };
let currentFilter = 'Todos';
let currentCommunity = DEFAULT_COMMUNITY;
let selectedMarker = null;
let geolocationMarker = null;
let reportCoordinates = null;
let selectingReportLocation = false;
let registering = false;
let reverseLookupTimer = null;
let reverseLookupSequence = 0;
let pendingReportPayload = null;
let resetToken = null;
let incidentsSignature = '';
const GROUP_RADIUS_M = 20; // incidencias a menos de esta distancia comparten marcador
const markersByIncident = new Map();

function escapeHtml(value = '') {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

function showMessage(message, isError = false) {
  const target = $('#appMessage');
  target.textContent = message;
  target.classList.toggle('error', isError);
  window.clearTimeout(showMessage.timeout);
  showMessage.timeout = window.setTimeout(() => { target.textContent = ''; }, 6000);
}

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (options.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(url, { ...options, headers });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    throw Object.assign(new Error(data?.message || `Error ${response.status}`), { status: response.status, data });
  }
  return data;
}

function isOperator() {
  return user && ['operador', 'administrador'].includes(user.role);
}

function typeInfo(type) {
  return meta.types.find((item) => item.name === type);
}

function typeColor(type) {
  return typeInfo(type)?.color || '#64748b';
}

function statusClass(status) {
  return { 'abierto': 'status-open', 'en proceso': 'status-progress', 'resuelto': 'status-resolved' }[status] || 'status-open';
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

function formatDate(value) {
  if (!value || Number.isNaN(new Date(value).getTime())) return 'Fecha no disponible';
  return new Intl.DateTimeFormat('es-CL', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}

function timeUntil(value) {
  if (!value) return '';
  const ms = new Date(value).getTime() - Date.now();
  const abs = Math.abs(ms);
  const days = Math.floor(abs / 86400000);
  const hours = Math.floor((abs % 86400000) / 3600000);
  const text = days >= 1 ? `${days} d ${hours} h` : `${Math.max(1, hours)} h`;
  return ms > 0 ? `Caduca en ${text}` : `Caducó hace ${text}`;
}

/* ---------- Chile ---------- */
function pointInPolygon(lat, lng, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [lat1, lng1] = polygon[j];
    const [lat2, lng2] = polygon[i];
    if ((lat1 > lat) !== (lat2 > lat) && lng < lng1 + ((lat - lat1) * (lng2 - lng1)) / (lat2 - lat1)) inside = !inside;
  }
  return inside;
}

function isInChile(lat, lng) {
  if (!meta.chile.polygon.length) return true; // sin metadatos, valida el servidor
  if (pointInPolygon(lat, lng, meta.chile.polygon)) return true;
  return meta.chile.islands.some(([s, w, n, e]) => lat >= s && lat <= n && lng >= w && lng <= e);
}

const OUTSIDE_CHILE = 'Solo se pueden reportar incidencias dentro de Chile.';

/* ---------- Filtros y orden ---------- */
function veracityMatches(incident, filter) {
  if (filter === 'confirmados') return ['confirmado_comunidad', 'verificado'].includes(incident.veracity);
  if (filter === 'por_confirmar') return incident.veracity === 'sin_verificar';
  if (filter === 'dudosos') return ['probablemente_falso', 'falso'].includes(incident.veracity);
  return true;
}

function filteredIncidents() {
  const query = $('#incidentSearch').value.trim().toLocaleLowerCase('es');
  const status = $('#statusFilter').value;
  const veracity = $('#veracityFilter').value;
  const order = $('#sortOrder').value;
  const result = incidents.filter((incident) =>
    (currentFilter === 'Todos' || incident.type === currentFilter) &&
    (status === 'Todos' || incident.status === status) &&
    veracityMatches(incident, veracity) &&
    (!query || `${incident.type} ${incident.description}`.toLocaleLowerCase('es').includes(query))
  );
  const byTime = (a, b) => new Date(b.time) - new Date(a.time);
  if (order === 'antiguos') result.sort((a, b) => -byTime(a, b));
  else if (order === 'confirmados') result.sort((a, b) => (b.veracityScore - a.veracityScore) || byTime(a, b));
  else result.sort(byTime);
  return result;
}

/* ---------- Mapa ---------- */
/** Agrupa por cercanía real (no por coordenadas redondeadas): así ningún marcador queda tapado por otro. */
function groupByLocation(list) {
  const groups = [];
  list.forEach((incident) => {
    const point = [Number(incident.lat), Number(incident.lng)];
    const group = groups.find((item) => map.distance(item.point, point) <= GROUP_RADIUS_M);
    if (group) group.items.push(incident);
    else groups.push({ point, items: [incident] });
  });
  return groups.map((group) => group.items);
}

function popupContent(group, selectedId = null) {
  const items = group.map((incident) => `<div class="popup-item${incident.id === selectedId ? ' selected' : ''}">
    <strong>${escapeHtml(incident.type)}</strong> <span class="veracity-chip ${veracityClass(incident.veracity)}">${escapeHtml(incident.veracityLabel)}</span>
    <p>${escapeHtml(incident.description.length > 140 ? `${incident.description.slice(0, 140)}…` : incident.description)}</p>
    <span>${escapeHtml(incident.status || 'abierto')} · ${escapeHtml(formatDate(incident.time))}</span>
    <button type="button" class="text-button popup-focus" data-id="${escapeHtml(incident.id)}">Ver detalle y votar</button>
  </div>`).join('');
  const heading = group.length > 1 ? `<p class="popup-count">${group.length} incidencias en este punto</p>` : '';
  return `<div class="popup-card">${heading}${items}</div>`;
}

function renderMarkers() {
  incidentsLayer.clearLayers();
  markersByIncident.clear();
  const list = filteredIncidents();
  groupByLocation(list).forEach((group) => {
    const lead = group[0];
    const doubtful = ['probablemente_falso', 'falso'].includes(lead.veracity);
    const confirmed = ['confirmado_comunidad', 'verificado'].includes(lead.veracity);
    const marker = L.circleMarker([Number(lead.lat), Number(lead.lng)], {
      radius: (lead.priority === 'crítica' ? 11 : 8) + (group.length > 1 ? 2 : 0),
      color: group.length > 1 ? '#173742' : typeColor(lead.type),
      fillColor: typeColor(lead.type),
      fillOpacity: doubtful ? 0.3 : 0.88,
      dashArray: doubtful ? '3 3' : null,
      weight: confirmed || group.length > 1 ? 3 : 2
    }).addTo(incidentsLayer);
    marker.bindPopup(popupContent(group), { maxHeight: 280 });
    marker.on('popupclose', () => marker.setPopupContent(popupContent(group)));
    marker.bindTooltip(group.length > 1 ? `${group.length} incidencias` : lead.type);
    marker.incidentGroup = group;
    group.forEach((incident) => markersByIncident.set(incident.id, marker));
  });
  renderIncidentList();
}

function openIncidentOnMap(incidentId) {
  const marker = markersByIncident.get(incidentId);
  const incident = incidents.find((item) => item.id === incidentId);
  if (!marker || !incident) {
    showMessage('Esta incidencia no se muestra con los filtros actuales.', true);
    return;
  }
  map.flyTo([incident.lat, incident.lng], Math.max(map.getZoom(), 12));
  marker.setPopupContent(popupContent(marker.incidentGroup, incidentId));
  marker.openPopup();
}

function focusIncidentCard(incidentId) {
  const card = document.querySelector(`.incident-card[data-id="${CSS.escape(incidentId)}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.classList.add('highlight');
  window.setTimeout(() => card.classList.remove('highlight'), 2500);
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('.popup-focus');
  if (button) focusIncidentCard(button.dataset.id);
});

/* ---------- Lista de incidencias ---------- */
function managementControls(incident) {
  const assignOptions = operators.map((operator) =>
    `<option value="${escapeHtml(operator.id)}" ${incident.assignedTo?.id === operator.id ? 'selected' : ''}>${escapeHtml(operator.name)} · ${escapeHtml(operator.role)}</option>`
  ).join('');
  const veracityOptions = [['sin_verificar', 'Sin verificar (votos)'], ['verificado', 'Verificado por operador'], ['falso', 'Falso (descartar)']]
    .map(([value, label]) => `<option value="${value}" ${incident.veracity === value ? 'selected' : ''}>${label}</option>`).join('');
  const automatic = ['confirmado_comunidad', 'probablemente_falso'].includes(incident.veracity)
    ? `<option value="" selected disabled>Automático: ${escapeHtml(incident.veracityLabel)}</option>` : '';
  return `<div class="management-controls">
    <label>Estado<select class="incident-status" data-id="${escapeHtml(incident.id)}"><option value="abierto" ${incident.status === 'abierto' ? 'selected' : ''}>Abierto</option><option value="en proceso" ${incident.status === 'en proceso' ? 'selected' : ''}>En proceso</option><option value="resuelto" ${incident.status === 'resuelto' ? 'selected' : ''}>Resuelto</option></select></label>
    <label>Prioridad<select class="incident-priority" data-id="${escapeHtml(incident.id)}"><option value="baja" ${incident.priority === 'baja' ? 'selected' : ''}>Baja</option><option value="media" ${incident.priority === 'media' ? 'selected' : ''}>Media</option><option value="alta" ${incident.priority === 'alta' ? 'selected' : ''}>Alta</option><option value="crítica" ${incident.priority === 'crítica' ? 'selected' : ''}>Crítica</option></select></label>
    <label>Veracidad<select class="incident-veracity" data-id="${escapeHtml(incident.id)}">${automatic}${veracityOptions}</select></label>
    <label>Asignar a<select class="incident-assignee" data-id="${escapeHtml(incident.id)}"><option value="">Sin asignar</option>${assignOptions}</select></label>
    <div class="comment-compose"><input class="comment-input" data-id="${escapeHtml(incident.id)}" maxlength="1000" placeholder="Comentario visible para todos" aria-label="Comentario para ${escapeHtml(incident.type)}" /><button class="secondary-button add-comment" data-id="${escapeHtml(incident.id)}" type="button">Comentar</button></div>
    <div class="moderation-row">
      <button class="secondary-button renew-incident" data-id="${escapeHtml(incident.id)}" type="button">Renovar vigencia</button>
      ${incident.hidden ? `<button class="secondary-button restore-incident" data-id="${escapeHtml(incident.id)}" type="button">Restaurar</button>` : `<button class="secondary-button hide-incident" data-id="${escapeHtml(incident.id)}" type="button">Ocultar</button>`}
    </div>
  </div>`;
}

function voteControls(incident) {
  const id = escapeHtml(incident.id);
  const own = user && user.id === incident.reporterId;
  const counts = `<span class="vote-counts">${incident.confirmCount} confirman · ${incident.denyCount} no coinciden</span>`;
  if (!user) return `<div class="vote-row">${counts}<small>Inicia sesión para votar.</small></div>`;
  if (own) return `<div class="vote-row">${counts}<small>Tu reporte: otras personas pueden votarlo.</small></div>`;
  if (incident.status === 'resuelto' || incident.expired) return `<div class="vote-row">${counts}</div>`;
  const flagged = incident.myFlag
    ? `<button type="button" class="text-button unflag-incident" data-id="${id}">Retirar mi denuncia</button>`
    : `<button type="button" class="text-button flag-incident" data-id="${id}">⚑ Reportar abuso</button>`;
  return `<div class="vote-row">
    <button type="button" class="vote-btn vote-confirm ${incident.myVote === 1 ? 'active' : ''}" data-id="${id}" data-vote="confirmar" aria-pressed="${incident.myVote === 1}">✓ Confirmo</button>
    <button type="button" class="vote-btn vote-deny ${incident.myVote === -1 ? 'active' : ''}" data-id="${id}" data-vote="negar" aria-pressed="${incident.myVote === -1}">✗ No es cierto</button>
    ${counts}${flagged}
  </div>`;
}

function renderIncidentList() {
  const list = $('#incidentList');
  // Se recuerdan detalles abiertos, comentarios a medio escribir y el foco para restaurarlos tras dibujar.
  const openCards = [...list.querySelectorAll('details[open]')].map((item) => item.closest('.incident-card')?.dataset.id).filter(Boolean);
  const drafts = [...list.querySelectorAll('.comment-input')].filter((input) => input.value).map((input) => [input.dataset.id, input.value]);
  const focused = document.activeElement?.matches?.('.comment-input') && list.contains(document.activeElement)
    ? { id: document.activeElement.dataset.id, position: document.activeElement.selectionStart } : null;
  const filtered = filteredIncidents();
  $('#incidentCount').textContent = String(filtered.length);
  if (!filtered.length) {
    list.innerHTML = '<p class="empty-state">No hay incidencias que coincidan con los filtros.</p>';
    return;
  }

  list.innerHTML = filtered.map((incident) => {
    const own = Boolean(user && user.id === incident.reporterId);
    const canEdit = own && incident.status === 'abierto';
    const canDelete = own || user?.role === 'administrador';
    const comments = (incident.comments || []).map((comment) =>
      `<li><strong>${escapeHtml(comment.actor)}</strong><span>${escapeHtml(formatDate(comment.at))}</span><p>${escapeHtml(comment.text)}</p></li>`
    ).join('');
    const history = (incident.history || []).slice().reverse().map((entry) =>
      `<li><span>${escapeHtml(formatDate(entry.at))}</span><p>${escapeHtml(entry.action)} · ${escapeHtml(entry.actor)}</p></li>`
    ).join('');
    const notes = [
      incident.hidden ? '<p class="assignee-label warn">Oculta al público por denuncias.</p>' : '',
      incident.expired ? '<p class="assignee-label warn">Caducada: ya no se muestra al público.</p>' : '',
      incident.veracity === 'falso' && own ? '<p class="assignee-label warn">Un operador la descartó como falsa.</p>' : ''
    ].join('');
    return `<article class="incident-card${incident.hidden || incident.expired ? ' inactive' : ''}" data-id="${escapeHtml(incident.id)}">
      <div class="card-topline"><span class="type-chip" style="--type-color:${typeColor(incident.type)}">${escapeHtml(incident.type)}</span><span class="status-chip ${statusClass(incident.status)}">${escapeHtml(incident.status || 'abierto')}</span></div>
      <div class="veracity-row"><span class="veracity-chip ${veracityClass(incident.veracity)}">${escapeHtml(incident.veracityLabel)}</span>${incident.reporterLevel ? `<span class="reputation-badge" title="Reputación de quien reportó">Reporta: ${escapeHtml(incident.reporterLevel)}</span>` : ''}</div>
      <h3>${escapeHtml(incident.type)}</h3><p class="incident-description">${escapeHtml(incident.description)}</p>
      <div class="incident-meta"><span>${escapeHtml(formatDate(incident.time))}</span><span class="priority-label priority-${escapeHtml(incident.priority || 'media')}">${escapeHtml(incident.priority || 'media')} prioridad</span></div>
      <p class="expiry-label">${escapeHtml(timeUntil(incident.expiresAt))}${incident.community ? ` · ${escapeHtml(incident.community.name)}` : ''}</p>
      ${notes}
      ${incident.assignedTo ? `<p class="assignee-label">Responsable: ${escapeHtml(incident.assignedTo.name)}</p>` : ''}
      ${incident.resolvedAt ? `<p class="assignee-label">Resuelta: ${escapeHtml(formatDate(incident.resolvedAt))}</p>` : ''}
      ${voteControls(incident)}
      ${canEdit ? `<button type="button" class="text-button edit-own-report" data-id="${escapeHtml(incident.id)}">Editar mi reporte</button>` : ''}
      ${canDelete ? `<button type="button" class="text-button delete-report" data-id="${escapeHtml(incident.id)}">${own ? 'Eliminar mi reporte' : 'Eliminar incidencia'}</button>` : ''}
      ${isOperator() ? managementControls(incident) : ''}
      ${(comments || history) ? `<details class="history-details"><summary>Actividad (${(incident.comments || []).length} comentarios)</summary>${comments ? `<h4>Comentarios</h4><ul class="activity-list">${comments}</ul>` : ''}${history ? `<h4>Historial de cambios</h4><ul class="activity-list">${history}</ul>` : ''}</details>` : ''}
      <button type="button" class="locate-incident" data-id="${escapeHtml(incident.id)}">Ver en el mapa ↗</button>
    </article>`;
  }).join('');

  openCards.forEach((id) => list.querySelector(`.incident-card[data-id="${CSS.escape(id)}"] details`)?.setAttribute('open', ''));
  drafts.forEach(([id, value]) => {
    const input = list.querySelector(`.comment-input[data-id="${CSS.escape(id)}"]`);
    if (input) input.value = value;
  });
  if (focused) {
    const input = list.querySelector(`.comment-input[data-id="${CSS.escape(focused.id)}"]`);
    if (input) {
      input.focus();
      if (focused.position !== null) input.setSelectionRange(focused.position, focused.position);
    }
  }
}

/** Con `quiet` (refresco automático) no se redibuja nada si los datos no cambiaron. Devuelve si se redibujó. */
async function loadIncidents({ quiet = false } = {}) {
  const params = new URLSearchParams();
  if (currentCommunity) params.set('community', currentCommunity);
  if (isOperator() && $('#includeInactive').checked) params.set('include', 'all');
  const fresh = await api(`/api/incidents?${params}`);
  const signature = JSON.stringify(fresh);
  if (quiet && signature === incidentsSignature) return false;
  incidentsSignature = signature;
  incidents = fresh;
  renderMarkers();
  return true;
}

async function refreshDashboard() {
  if (!isOperator()) {
    $('#dashboard').classList.add('hidden');
    $('#userManagement').classList.add('hidden');
    return;
  }
  const query = currentCommunity ? `?community=${encodeURIComponent(currentCommunity)}` : '';
  const [stats, people] = await Promise.all([api(`/api/admin/stats${query}`), api('/api/users')]);
  operators = people.filter((person) => ['operador', 'administrador'].includes(person.role));
  $('#dashboard').classList.remove('hidden');
  $('#statTotal').textContent = stats.total;
  $('#statOpen').textContent = stats.open;
  $('#statProgress').textContent = stats.inProgress;
  $('#statResolved').textContent = stats.resolved;
  $('#statPriority').textContent = stats.highPriority;
  $('#statConfirmed').textContent = stats.confirmed;
  $('#statHidden').textContent = stats.hidden;
  const isAdmin = user.role === 'administrador';
  $('#userManagement').classList.toggle('hidden', !isAdmin);
  if (isAdmin) {
    $('#userList').innerHTML = people.map((person) =>
      `<article class="user-row"><div><strong>${escapeHtml(person.name)}</strong><span>${escapeHtml(person.email)} · reputación ${escapeHtml(person.reputation)} (${escapeHtml(person.reputationLevel)})</span></div><label>Rol<select class="user-role-select" data-id="${escapeHtml(person.id)}" ${person.id === user.id ? 'disabled' : ''}><option value="ciudadano" ${person.role === 'ciudadano' ? 'selected' : ''}>Ciudadano</option><option value="operador" ${person.role === 'operador' ? 'selected' : ''}>Operador</option><option value="administrador" ${person.role === 'administrador' ? 'selected' : ''}>Administrador</option></select></label></article>`
    ).join('');
  }
}

async function refreshView({ quiet = false } = {}) {
  const changed = await loadIncidents({ quiet });
  await refreshDashboard();
  if (changed) renderIncidentList();
}

function updateUserControls() {
  $('#userLabel').textContent = user ? `${user.name} · ${user.role} · reputación ${user.reputation}` : 'Visitante';
  $('#authBtn').textContent = user ? 'Cerrar sesión' : 'Ingresar';
  $('#adminPanelLink').classList.toggle('hidden', user?.role !== 'administrador');
  $('#openReportBtn').disabled = !user;
  $('#openReportBtn').title = user ? '' : 'Inicia sesión para crear un reporte';
  $('#notificationsBtn').classList.toggle('hidden', !user);
  $('#dashboard').classList.toggle('hidden', !isOperator());
  $('#includeInactiveWrap').classList.toggle('hidden', !isOperator());
}

/* ---------- Metadatos y comunidades ---------- */
function populateMeta() {
  $('#incidentType').innerHTML = '<option value="">Selecciona un tipo</option>' +
    meta.types.map((item) => `<option>${escapeHtml(item.name)}</option>`).join('');
  $('#typeFilters').innerHTML = '<button class="filter-btn active" data-filter="Todos" type="button">Todos</button>' +
    meta.types.map((item) => `<button class="filter-btn" data-filter="${escapeHtml(item.name)}" type="button">${escapeHtml(item.short)}</button>`).join('');
  $('#mapLegend').innerHTML = '<strong>Tipos de incidencia</strong>' +
    meta.types.map((item) => `<span><i class="legend-dot" style="background:${item.color}"></i>${escapeHtml(item.short)}</span>`).join('');
  $('#flagReason').innerHTML = meta.flagReasons.map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join('');
  const options = meta.communities.map((item) => `<option value="${escapeHtml(item.slug)}">${escapeHtml(item.name)}</option>`).join('');
  $('#communityFilter').innerHTML = `<option value="">Todo Chile</option>${options}`;
  $('#authCommunity').innerHTML = `<option value="">Sin elegir</option>${options}`;
}

function setCommunity(slug, { fly = true, persist = true } = {}) {
  currentCommunity = slug;
  $('#communityFilter').value = slug;
  if (persist) localStorage.setItem(communityKey, slug);
  const community = meta.communities.find((item) => item.slug === slug);
  if (fly) {
    if (community) map.flyTo([community.lat, community.lng], 8);
    else map.flyTo([-38.5, -72.0], 4);
  }
}

$('#communityFilter').addEventListener('change', async (event) => {
  setCommunity(event.target.value);
  if (user) {
    try {
      const result = await api('/api/auth/me', { method: 'PUT', body: JSON.stringify({ community: event.target.value || null }) });
      user = result.user;
    } catch (error) { console.warn('No se pudo guardar la comunidad del usuario:', error); }
  }
  try { await refreshView(); } catch (error) { showMessage(error.message, true); }
});

/* ---------- Reportes ---------- */
function resetReportForm() {
  selectingReportLocation = false;
  $('#mapSelectionControls').classList.add('hidden');
  reverseLookupSequence += 1;
  window.clearTimeout(reverseLookupTimer);
  $('#reportForm').reset();
  $('#editIncidentId').value = '';
  $('#latInput').value = '';
  $('#lngInput').value = '';
  $('#latValue').textContent = '—';
  $('#lngValue').textContent = '—';
  $('#locationLabel').textContent = 'Busca un lugar o selecciona un punto en el mapa.';
  $('#reportTitle').textContent = 'Nueva incidencia';
  $('#reportError').textContent = '';
  reportCoordinates = null;
  pendingReportPayload = null;
  if (selectedMarker) {
    map.removeLayer(selectedMarker);
    selectedMarker = null;
  }
}

function openReport(incident = null) {
  if (!user) {
    showMessage('Inicia sesión para crear una incidencia.', true);
    authDialogMode(false);
    $('#authDialog').showModal();
    return;
  }
  selectingReportLocation = false;
  $('#mapSelectionControls').classList.add('hidden');
  resetReportForm();
  if (incident) {
    $('#reportTitle').textContent = 'Editar mi incidencia';
    $('#editIncidentId').value = incident.id;
    $('#incidentType').value = incident.type;
    $('#incidentDescription').value = incident.description;
    const locked = incident.confirmCount > 0 || incident.denyCount > 0;
    $('#incidentType').disabled = locked;
    setReportLocation(Number(incident.lat), Number(incident.lng), locked
      ? 'Ubicación fija: el reporte ya recibió votos'
      : 'Ubicación actual de la incidencia');
    if (locked) $('#selectLocationBtn').disabled = true;
  } else {
    $('#incidentType').disabled = false;
    $('#selectLocationBtn').disabled = false;
  }
  $('#reportDialog').showModal();
}

function setReportLocation(lat, lng, label = 'Ubicación seleccionada en el mapa') {
  const sequence = ++reverseLookupSequence;
  window.clearTimeout(reverseLookupTimer);
  reportCoordinates = { lat, lng };
  $('#latInput').value = lat;
  $('#lngInput').value = lng;
  $('#latValue').textContent = lat.toFixed(5);
  $('#lngValue').textContent = lng.toFixed(5);
  $('#locationLabel').textContent = label;
  if (selectedMarker) map.removeLayer(selectedMarker);
  selectedMarker = L.marker([lat, lng], { draggable: !$('#selectLocationBtn').disabled }).addTo(map);
  selectedMarker.on('dragend', async (event) => {
    const position = event.target.getLatLng();
    acceptReportLocation(position.lat, position.lng);
  });
  if (label === 'Ubicación seleccionada en el mapa') {
    reverseLookupTimer = window.setTimeout(async () => {
      try {
        const response = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lng)}&zoom=16`, { referrerPolicy: 'strict-origin-when-cross-origin' });
        if (!response.ok) throw new Error('Servicio de geocodificación no disponible.');
        const result = await response.json();
        if (sequence === reverseLookupSequence && result.display_name) $('#locationLabel').textContent = result.display_name;
      } catch (error) {
        console.warn('No se pudo resolver la dirección de las coordenadas:', error);
      }
    }, 800);
  }
}

/** Valida que el punto esté en Chile antes de aceptarlo como ubicación del reporte. */
function acceptReportLocation(lat, lng, label) {
  if (!isInChile(lat, lng)) {
    $('#reportError').textContent = OUTSIDE_CHILE;
    showMessage(OUTSIDE_CHILE, true);
    return false;
  }
  $('#reportError').textContent = '';
  setReportLocation(lat, lng, label);
  return true;
}

function getGeolocation() {
  if (!navigator.geolocation) {
    showMessage('Este navegador no permite obtener la ubicación GPS.', true);
    return;
  }
  navigator.geolocation.getCurrentPosition((position) => {
    const { latitude, longitude } = position.coords;
    map.flyTo([latitude, longitude], 13);
    if (geolocationMarker) map.removeLayer(geolocationMarker);
    geolocationMarker = L.circleMarker([latitude, longitude], { radius: 9, color: '#0f766e', fillColor: '#14b8a6', fillOpacity: 0.8 }).addTo(map).bindPopup('Tu ubicación aproximada').openPopup();
    if ($('#reportDialog').open) acceptReportLocation(latitude, longitude, 'Ubicación obtenida por GPS');
  }, (error) => {
    const message = error.code === error.PERMISSION_DENIED ? 'Permite el acceso a la ubicación para usar el GPS.' : 'No se pudo obtener tu ubicación. Inténtalo nuevamente.';
    showMessage(message, true);
  }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 });
}

map.on('click', (event) => {
  if (selectingReportLocation) {
    if (!isInChile(event.latlng.lat, event.latlng.lng)) {
      showMessage(OUTSIDE_CHILE, true);
      return; // sigue en modo selección
    }
    selectingReportLocation = false;
    $('#mapSelectionControls').classList.add('hidden');
    setReportLocation(event.latlng.lat, event.latlng.lng);
    $('#reportDialog').showModal();
  } else if ($('#reportDialog').open) {
    acceptReportLocation(event.latlng.lat, event.latlng.lng);
  }
});

/* ---------- Acciones sobre incidencias ---------- */
async function castVote(id, value) {
  const incident = incidents.find((item) => item.id === id);
  const retract = incident && ((value === 'confirmar' && incident.myVote === 1) || (value === 'negar' && incident.myVote === -1));
  await api(`/api/incidents/${encodeURIComponent(id)}/vote`, retract
    ? { method: 'DELETE' }
    : { method: 'POST', body: JSON.stringify({ value }) });
  await refreshView();
  await refreshNotifications();
  showMessage(retract ? 'Retiraste tu voto.' : 'Gracias, tu voto fue registrado.');
}

$('#incidentList').addEventListener('click', async (event) => {
  const target = event.target;
  const id = target.dataset.id;
  const incident = incidents.find((item) => item.id === id);
  try {
    if (target.matches('.locate-incident') && incident) openIncidentOnMap(id);
    if (target.matches('.edit-own-report') && incident) openReport(incident);
    if (target.matches('.vote-btn')) await castVote(id, target.dataset.vote);
    if (target.matches('.flag-incident')) {
      $('#flagIncidentId').value = id;
      $('#flagError').textContent = '';
      $('#flagDialog').showModal();
    }
    if (target.matches('.unflag-incident')) {
      await api(`/api/incidents/${encodeURIComponent(id)}/flag`, { method: 'DELETE' });
      await refreshView();
      showMessage('Retiraste tu denuncia.');
    }
    if (target.matches('.delete-report') && incident && window.confirm(`¿Eliminar el reporte "${incident.type}"? Esta acción no se puede deshacer.`)) {
      await api(`/api/incidents/${encodeURIComponent(id)}`, { method: 'DELETE' });
      await refreshView();
      showMessage('Incidencia eliminada.');
    }
    if (target.matches('.add-comment')) {
      const input = $(`.comment-input[data-id="${CSS.escape(id)}"]`);
      await api(`/api/incidents/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ comment: input.value }) });
      await refreshView();
      showMessage('Comentario añadido y registrado en el historial.');
    }
    if (target.matches('.renew-incident') || target.matches('.hide-incident') || target.matches('.restore-incident')) {
      const body = target.matches('.renew-incident') ? { renew: true } : { hidden: target.matches('.hide-incident') };
      await api(`/api/incidents/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) });
      await refreshView();
      showMessage('Cambios guardados.');
    }
  } catch (error) { showMessage(error.message, true); }
});

$('#incidentList').addEventListener('change', async (event) => {
  const target = event.target;
  const property = target.classList.contains('incident-status') ? 'status'
    : target.classList.contains('incident-priority') ? 'priority'
      : target.classList.contains('incident-veracity') ? 'veracity'
        : target.classList.contains('incident-assignee') ? 'assignedTo' : null;
  if (!property) return;
  try {
    await api(`/api/incidents/${encodeURIComponent(target.dataset.id)}`, {
      method: 'PUT',
      body: JSON.stringify({ [property]: property === 'assignedTo' ? (target.value || null) : target.value })
    });
    await refreshView();
    showMessage('Cambios guardados. Se actualizó el historial de la incidencia.');
  } catch (error) { showMessage(error.message, true); }
});

$('#userList').addEventListener('change', async (event) => {
  const target = event.target;
  if (!target.matches('.user-role-select')) return;
  try {
    await api(`/api/users/${encodeURIComponent(target.dataset.id)}/role`, {
      method: 'PUT',
      body: JSON.stringify({ role: target.value })
    });
    await refreshDashboard();
    showMessage('Rol de usuario actualizado.');
  } catch (error) {
    showMessage(error.message, true);
    await refreshDashboard();
  }
});

$('#flagForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const result = await api(`/api/incidents/${encodeURIComponent($('#flagIncidentId').value)}/flag`, {
      method: 'POST',
      body: JSON.stringify({ reason: $('#flagReason').value })
    });
    $('#flagDialog').close();
    await refreshView();
    showMessage(result.hidden ? 'Gracias: con tu denuncia el reporte quedó oculto para revisión.' : 'Denuncia enviada. Gracias por ayudar a mantener la información confiable.');
  } catch (error) { $('#flagError').textContent = error.message; }
});

/* ---------- Filtros ---------- */
$('#typeFilters').addEventListener('click', (event) => {
  const button = event.target.closest('.filter-btn');
  if (!button) return;
  currentFilter = button.dataset.filter;
  document.querySelectorAll('.filter-btn').forEach((item) => item.classList.toggle('active', item === button));
  renderMarkers();
});
['#statusFilter', '#veracityFilter', '#sortOrder'].forEach((selector) => $(selector).addEventListener('change', renderMarkers));
$('#incidentSearch').addEventListener('input', renderMarkers);
$('#includeInactive').addEventListener('change', () => refreshView().catch((error) => showMessage(error.message, true)));
$('#openReportBtn').addEventListener('click', () => openReport());
$('#locateBtn').addEventListener('click', getGeolocation);
$('#selectLocationBtn').addEventListener('click', () => {
  selectingReportLocation = true;
  $('#mapSelectionControls').classList.remove('hidden');
  $('#reportDialog').close();
});
$('#cancelMapSelection').addEventListener('click', () => {
  selectingReportLocation = false;
  $('#mapSelectionControls').classList.add('hidden');
  $('#reportDialog').showModal();
});
$('#closeReportBtn').addEventListener('click', () => $('#reportDialog').close());
$('#cancelReportBtn').addEventListener('click', () => $('#reportDialog').close());
$('#reportDialog').addEventListener('close', () => {
  if (!selectingReportLocation) resetReportForm();
});
document.querySelectorAll('.close-dialog').forEach((button) => button.addEventListener('click', () => button.closest('dialog').close()));

/* ---------- Envío del reporte + detección de duplicados ---------- */
async function submitReport(payload, editId) {
  await api(editId ? `/api/incidents/${encodeURIComponent(editId)}` : '/api/incidents', {
    method: editId ? 'PUT' : 'POST',
    body: JSON.stringify(payload)
  });
  $('#duplicateDialog').close();
  $('#reportDialog').close();
  await refreshView();
  showMessage(editId ? 'Tu reporte se actualizó.' : 'Incidencia registrada correctamente.');
}

function showDuplicates(duplicates) {
  $('#duplicateList').innerHTML = duplicates.map((item) => {
    const own = user && user.id === item.reporterId;
    return `<article class="dup-item">
      <strong>${escapeHtml(item.type)}</strong> <span class="veracity-chip ${veracityClass(item.veracity)}">${escapeHtml(item.veracityLabel)}</span>
      <p>${escapeHtml(item.description)}</p>
      <small>A ${escapeHtml(item.distanceM)} m de tu punto · ${escapeHtml(formatDate(item.time))} · ${escapeHtml(item.confirmCount)} confirmaciones</small>
      ${own ? '<small>Este reporte es tuyo.</small>' : `<button type="button" class="primary-button dup-same" data-id="${escapeHtml(item.id)}">Es el mismo: confirmarlo</button>`}
    </article>`;
  }).join('');
  $('#duplicateDialog').showModal();
}

$('#duplicateList').addEventListener('click', async (event) => {
  const button = event.target.closest('.dup-same');
  if (!button) return;
  button.disabled = true;
  try {
    await api(`/api/incidents/${encodeURIComponent(button.dataset.id)}/vote`, { method: 'POST', body: JSON.stringify({ value: 'confirmar' }) });
    $('#duplicateDialog').close();
    $('#reportDialog').close();
    await refreshView();
    showMessage('Confirmaste el reporte existente en lugar de crear uno nuevo. ¡Gracias!');
  } catch (error) {
    button.disabled = false;
    showMessage(error.message, true);
  }
});

$('#duplicateCreateBtn').addEventListener('click', async () => {
  if (!pendingReportPayload) return;
  try {
    await submitReport({ ...pendingReportPayload, confirmDistinct: true }, '');
  } catch (error) {
    $('#duplicateDialog').close();
    $('#reportError').textContent = error.message;
  }
});

$('#reportForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = {
    type: $('#incidentType').value,
    description: $('#incidentDescription').value.trim(),
    lat: Number($('#latInput').value),
    lng: Number($('#lngInput').value)
  };
  if (!reportCoordinates) {
    $('#reportError').textContent = 'Selecciona una ubicación en el mapa, usa GPS o busca un lugar.';
    return;
  }
  if (!isInChile(payload.lat, payload.lng)) {
    $('#reportError').textContent = OUTSIDE_CHILE;
    return;
  }
  const editId = $('#editIncidentId').value;
  if (editId && $('#incidentType').disabled) delete payload.type; // con votos no se puede cambiar el tipo
  if (editId && $('#selectLocationBtn').disabled) { delete payload.lat; delete payload.lng; }
  try {
    pendingReportPayload = editId ? null : payload;
    await submitReport(payload, editId);
  } catch (error) {
    if (error.status === 409 && error.data?.code === 'possible_duplicate') {
      showDuplicates(error.data.duplicates);
    } else {
      $('#reportError').textContent = error.message;
    }
  }
});

/* ---------- Búsqueda de lugares ---------- */
$('#locationSearchForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = $('#locationSearch').value.trim();
  if (!query) return;
  const community = meta.communities.find((item) => item.slug === currentCommunity);
  const scope = community ? `${community.name}, Chile` : 'Chile';
  try {
    const response = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=cl&q=${encodeURIComponent(`${query}, ${scope}`)}`, { referrerPolicy: 'strict-origin-when-cross-origin' });
    if (!response.ok) throw new Error('El servicio de búsqueda de lugares no está disponible.');
    const places = await response.json();
    if (!places.length) throw new Error('No se encontró esa ubicación en Chile.');
    const place = places[0];
    const lat = Number(place.lat);
    const lng = Number(place.lon);
    map.flyTo([lat, lng], 13);
    if ($('#reportDialog').open) acceptReportLocation(lat, lng, place.display_name);
    else showMessage(`Ubicación encontrada: ${place.display_name}`);
  } catch (error) { showMessage(error.message, true); }
});

$('#geoJsonInput').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const geoJson = JSON.parse(await file.text());
    if (!['FeatureCollection', 'Feature', 'Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon', 'GeometryCollection'].includes(geoJson.type)) {
      throw new Error('El archivo no contiene una capa GeoJSON válida.');
    }
    geoLayers.clearLayers();
    const layer = L.geoJSON(geoJson, {
      style: { color: '#0f766e', weight: 3, fillColor: '#14b8a6', fillOpacity: 0.18 },
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, { radius: 6, color: '#0f766e', fillColor: '#14b8a6', fillOpacity: 0.85 })
    }).addTo(geoLayers);
    if (layer.getLayers().length) map.fitBounds(layer.getBounds(), { padding: [25, 25], maxZoom: 13 });
    showMessage(`Capa "${file.name}" cargada en el mapa.`);
  } catch (error) { showMessage(error.message || 'No se pudo cargar el archivo GeoJSON.', true); }
  event.target.value = '';
});

/* ---------- Autenticación y recuperación de contraseña ---------- */
function authDialogMode(isRegistering) {
  registering = isRegistering;
  $('#authTitle').textContent = registering ? 'Crear cuenta' : 'Ingresar';
  $('#nameField').classList.toggle('hidden', !registering);
  $('#communityField').classList.toggle('hidden', !registering);
  $('#forgotPasswordBtn').classList.toggle('hidden', registering);
  $('#authName').required = registering;
  $('#authPassword').autocomplete = registering ? 'new-password' : 'current-password';
  $('#authModeToggle').textContent = registering ? '¿Ya tienes cuenta? Ingresa' : '¿No tienes cuenta? Regístrate';
  $('#authError').textContent = '';
}

$('#authBtn').addEventListener('click', async () => {
  if (!user) {
    authDialogMode(false);
    $('#authDialog').showModal();
    return;
  }
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (error) { console.error('No se pudo cerrar la sesión en el servidor:', error); }
  token = null;
  user = null;
  localStorage.removeItem(tokenKey);
  updateUserControls();
  await refreshView();
  showMessage('Sesión cerrada.');
});

$('#authModeToggle').addEventListener('click', () => authDialogMode(!registering));
async function establishSession(result) {
  token = result.token;
  user = result.user;
  localStorage.setItem(tokenKey, token);
  $('#authDialog').close();
  $('#authForm').reset();
  if (user.community) setCommunity(user.community.slug);
  updateUserControls();
  await refreshView();
  await refreshNotifications();
  showMessage(`Bienvenido/a, ${user.name}.`);
}

$('#authForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#authError').textContent = '';
  try {
    const credentials = {
      email: $('#authEmail').value,
      password: $('#authPassword').value
    };
    if (registering) {
      const result = await api('/api/auth/register', {
        method: 'POST',
        body: JSON.stringify({ ...credentials, name: $('#authName').value, community: $('#authCommunity').value || null })
      });
      await establishSession(result);
      return;
    }
    const result = await api('/api/auth/login', { method: 'POST', body: JSON.stringify(credentials) });
    await establishSession(result);
  } catch (error) { $('#authError').textContent = error.message; }
});

$('#forgotPasswordBtn').addEventListener('click', () => {
  $('#resetEmail').value = $('#authEmail').value;
  $('#resetRequestMessage').textContent = '';
  $('#authDialog').close();
  $('#resetRequestDialog').showModal();
});

$('#resetRequestForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#resetRequestMessage').textContent = '';
  try {
    const result = await api('/api/auth/password-reset/request', { method: 'POST', body: JSON.stringify({ email: $('#resetEmail').value }) });
    $('#resetRequestMessage').textContent = result.message;
  } catch (error) { $('#resetRequestMessage').textContent = error.message; }
});

$('#resetConfirmForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#resetConfirmError').textContent = '';
  if ($('#resetPassword').value !== $('#resetPasswordRepeat').value) {
    $('#resetConfirmError').textContent = 'Las contraseñas no coinciden.';
    return;
  }
  try {
    const result = await api('/api/auth/password-reset/confirm', {
      method: 'POST',
      body: JSON.stringify({ token: resetToken, password: $('#resetPassword').value })
    });
    resetToken = null;
    token = null;
    user = null;
    localStorage.removeItem(tokenKey);
    $('#resetConfirmForm').reset();
    $('#resetConfirmDialog').close();
    updateUserControls();
    showMessage(result.message);
    authDialogMode(false);
    $('#authDialog').showModal();
  } catch (error) { $('#resetConfirmError').textContent = error.message; }
});

/** El enlace del correo trae el token en el fragmento (#reset=...), que no viaja al servidor ni en Referer. */
function handleResetLink() {
  const match = window.location.hash.match(/^#reset=([A-Za-z0-9_-]+)$/);
  if (!match) return;
  resetToken = match[1];
  window.history.replaceState(null, '', window.location.pathname + window.location.search);
  $('#resetConfirmDialog').showModal();
}

/* ---------- Notificaciones ---------- */
async function refreshNotifications() {
  if (!user) return;
  const notifications = await api('/api/notifications');
  const unread = notifications.filter((notification) => !notification.read).length;
  $('#notificationCount').textContent = unread;
  $('#notificationList').innerHTML = notifications.length
    ? notifications.map((notification) => `<article class="notification-item ${notification.read ? '' : 'unread'}"><p>${escapeHtml(notification.message)}</p><small>${escapeHtml(formatDate(notification.at))}</small></article>`).join('')
    : '<p class="empty-state">No tienes notificaciones.</p>';
}

$('#notificationsBtn').addEventListener('click', async () => {
  try {
    await refreshNotifications();
    $('#notificationsDialog').showModal();
    await api('/api/notifications/read', { method: 'PUT' });
    $('#notificationCount').textContent = '0';
  } catch (error) { showMessage(error.message, true); }
});

$('#menuToggle').addEventListener('click', () => {
  const expanded = $('#menuToggle').getAttribute('aria-expanded') === 'true';
  $('#menuToggle').setAttribute('aria-expanded', String(!expanded));
  $('#topMenu').classList.toggle('menu-open', !expanded);
});

async function initialize() {
  try {
    try {
      meta = await api('/api/meta');
    } catch (error) {
      showMessage(`No se pudo cargar la configuración: ${error.message}`, true);
    }
    populateMeta();
    if (token) {
      try {
        const result = await api('/api/auth/me');
        user = result.user;
      } catch {
        token = null;
        localStorage.removeItem(tokenKey);
      }
    }
    const saved = localStorage.getItem(communityKey);
    const initial = user?.community?.slug ?? (saved !== null ? saved : DEFAULT_COMMUNITY);
    setCommunity(meta.communities.some((item) => item.slug === initial) ? initial : '', { persist: false });
    updateUserControls();
    handleResetLink();
    await refreshView();
    if (user) await refreshNotifications();
  } catch (error) {
    showMessage(`No se pudieron cargar los reportes: ${error.message}`, true);
  }
}

initialize();
window.setInterval(async () => {
  if (!user) return;
  try {
    await refreshView({ quiet: true });
    await refreshNotifications();
  } catch (error) {
    console.error('No se pudieron actualizar las incidencias y notificaciones:', error);
  }
}, 60000);
