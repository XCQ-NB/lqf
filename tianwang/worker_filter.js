// ===================== 施工内容过滤逻辑（负责人由登录账号自动同步） =====================
var DEFAULT_WORKERS = [
  { id: 1, name: '施之锋', role: 'team_leader' },
  { id: 2, name: '黄宗耀', role: 'team_leader' },
  { id: 3, name: '何瑞琥', role: 'safety_officer' },
  { id: 4, name: '邹永丰', role: 'worker' },
  { id: 5, name: '胡庆斌', role: 'maintenance' },
  { id: 6, name: '黄晟', role: 'maintenance' },
  { id: 7, name: '林喜锋', role: 'worker' }
];

var _workersCache = null;

function getWorkers() {
  if (_workersCache) return _workersCache;
  try {
    var w = JSON.parse(localStorage.getItem("tianwang_workers"));
    if (w && w.length) { _workersCache = w; return w; }
  } catch(e) {}
  _workersCache = DEFAULT_WORKERS;
  return DEFAULT_WORKERS;
}

function getTeamLeaders(workers) {
  workers = workers || getWorkers();
  return workers.filter(function(w) { return w.role === 'team_leader'; });
}

function loadWorkersAsync(callback) {
  try {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', (window.API_BASE || '') + '/api/workers');
    xhr.onload = function() {
      try {
        var json = JSON.parse(xhr.responseText);
        if (json.success && json.data) {
          _workersCache = json.data.map(function(w) {
            return { id: w.id, name: w.name, role: w.role };
          });
          localStorage.setItem('tianwang_workers', JSON.stringify(_workersCache));
          if (callback) callback(_workersCache);
          return;
        }
      } catch(e) {}
      if (callback) callback(getWorkers());
    };
    xhr.onerror = function() { if (callback) callback(getWorkers()); };
    xhr.send();
  } catch(e) { if (callback) callback(getWorkers()); }
}

function getCurrentPersonName() {
  var hidden = document.getElementById("wm_person");
  if (hidden && hidden.value) return hidden.value;
  if (typeof getWorkerName === 'function') return getWorkerName() || '';
  if (typeof APP_SESSION !== 'undefined' && APP_SESSION) {
    return APP_SESSION.workerName || APP_SESSION.displayName || APP_SESSION.username || '';
  }
  return '';
}

function resetBuildOptions(role) {
  var sel = document.getElementById("wm_build");
  if (!sel) return;
  var allOptions = typeof BUILD_OPTIONS !== 'undefined' ? BUILD_OPTIONS : [];
  var roleAllow = typeof BUILD_ROLE_ALLOW !== 'undefined' ? BUILD_ROLE_ALLOW : { team_leader: true };
  var groupLabels = typeof BUILD_GROUP_LABELS !== 'undefined' ? BUILD_GROUP_LABELS : {};

  var allow = (role === "all") ? true :
    (Array.isArray(roleAllow[role])) ? roleAllow[role] :
    (roleAllow[role] === true) ? allOptions.map(function(o){ return o.val; }) : [];

  if (role === 'empty') {
    sel.style.display = 'none';
    sel.innerHTML = '<option value="">🔨 请先登录</option>';
    toggleFaultReason();
    if (typeof onBuildChange === 'function') onBuildChange();
    return;
  }

  sel.style.display = '';
  var curGroup = '';
  var html = '<option value="">🔨 选择施工阶段（必选）</option>';
  allOptions.forEach(function(o) {
    if (allow === true || allow.indexOf(o.val) >= 0) {
      if (o.group !== curGroup) {
        if (curGroup !== '') html += '</optgroup>';
        html += '<optgroup label="── ' + (groupLabels[o.group] || o.group) + ' ──">';
        curGroup = o.group;
      }
      html += '<option value="' + o.val + '">' + o.label + '</option>';
    }
  });
  if (curGroup !== '') html += '</optgroup>';
  sel.innerHTML = html;
  toggleFaultReason();
  if (typeof onBuildChange === 'function') onBuildChange();
}

function toggleFaultReason() {
  var buildSel = document.getElementById("wm_build");
  var reasonInput = document.getElementById("wm_fault_reason");
  var pointInput = document.getElementById("wm_point");
  if (!buildSel) return;

  if (buildSel.value === "故障处理") {
    if (reasonInput) { reasonInput.style.display = ""; reasonInput.required = true; }
    if (pointInput) { pointInput.style.display = ""; pointInput.required = true; }
  } else if (buildSel.value === "设备离线检查") {
    if (reasonInput) { reasonInput.style.display = "none"; reasonInput.value = ""; reasonInput.required = false; }
    if (pointInput) { pointInput.style.display = "none"; pointInput.value = ""; pointInput.required = false; }
  } else {
    if (reasonInput) { reasonInput.style.display = "none"; reasonInput.value = ""; reasonInput.required = false; }
    if (pointInput) { pointInput.style.display = ""; pointInput.required = true; }
  }
}

function initWorkerFilter() {
  var buildSel = document.getElementById("wm_build");
  if (!buildSel) { setTimeout(initWorkerFilter, 100); return; }
  if (typeof lockPersonSelect === 'function') lockPersonSelect();
  else if (getCurrentPersonName()) resetBuildOptions('all');
  else resetBuildOptions('empty');
  buildSel.addEventListener("change", function() {
    toggleFaultReason();
    if (typeof onBuildChange === 'function') onBuildChange();
  });
  toggleFaultReason();
  if (typeof onBuildChange === 'function') onBuildChange();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initWorkerFilter);
} else {
  initWorkerFilter();
}
