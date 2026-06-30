// ===================== 项目分类 & 施工内容配置（统一维护） =====================

var PROJECT_OPTIONS = [
  '红谷滩天网',
  '南昌县陆港天网',
  '南昌县禁鱼禁捕',
  '青云谱天网',
  '社会资源',
  '待定项目1',
  '待定项目2',
  '待定项目3'
];

var BUILD_OPTIONS = [
  { val: '安装位置', label: '1. 安装位置', group: '勘察' },
  { val: '监控方向', label: '2. 监控方向', group: '勘察' },
  { val: '光缆布放', label: '3. 光缆布放', group: '光缆' },
  { val: '光功率测试', label: '4. 光功率测试', group: '光缆' },
  { val: '电缆施工', label: '5. 电缆施工', group: '取电' },
  { val: '取电位置', label: '6. 取电位置', group: '取电' },
  { val: '箱体通电', label: '7. 箱体通电', group: '取电' },
  { val: '立杆方式', label: '8. 立杆方式', group: '杆件' },
  { val: '支臂安装', label: '9. 支臂安装', group: '杆件' },
  { val: '摄像头安装', label: '10. 摄像头安装', group: '设备' },
  { val: '摄像头序列号', label: '11. 摄像头序列号', group: '设备' },
  { val: '防雨箱安装', label: '12. 防雨箱安装', group: '设备' },
  { val: 'PING测试记录', label: '13. PING测试记录', group: '设备' },
  { val: '光猫编号', label: '14. 光猫编号', group: '设备' },
  { val: '安全检查', label: '15. 安全检查', group: '验收' },
  { val: '设备离线检查', label: '16. 设备离线检查', group: '验收' },
  { val: '故障处理', label: '17. 故障处理', group: '运维' }
];

// 计入站点施工进度（与手机端 1～15 编号一致，不含设备离线检查/故障处理）
var CONSTRUCTION_BUILDS = [
  '安装位置', '监控方向', '光缆布放', '光功率测试',
  '电缆施工', '取电位置', '箱体通电',
  '立杆方式', '支臂安装', '摄像头安装', '摄像头序列号',
  '防雨箱安装', 'PING测试记录', '光猫编号', '安全检查'
];

var NON_SITE_BUILDS = ['故障处理', '设备离线检查'];

var CONSTRUCTION_STAGE_ORDER = ['勘察', '光路', '取电', '杆件', '设备', '验收'];

var CONSTRUCTION_STAGE_DISPLAY = {
  勘察: '勘察阶段',
  光路: '光缆阶段',
  取电: '取电阶段',
  杆件: '杆件阶段',
  设备: '设备阶段',
  验收: '验收阶段',
  done: '全部完成',
  none: '未开始'
};

var BUILD_TO_STAGE = {
  '安装位置': '勘察', '监控方向': '勘察',
  '光缆布放': '光路', '光功率测试': '光路',
  '电缆施工': '取电', '取电位置': '取电', '箱体通电': '取电',
  '立杆方式': '杆件', '支臂安装': '杆件',
  '摄像头安装': '设备', '摄像头序列号': '设备',
  '防雨箱安装': '设备', 'PING测试记录': '设备', '光猫编号': '设备',
  '安全检查': '验收'
};

var BUILD_GROUP_LABELS = {
  勘察: '勘察阶段',
  光缆: '光缆阶段',
  取电: '取电阶段',
  杆件: '杆件阶段',
  设备: '设备阶段',
  验收: '验收阶段',
  运维: '运维阶段'
};

var BUILD_ROLE_ALLOW = {
  team_leader: true,
  project_manager: true,
  safety_officer: ['安全检查', '设备离线检查'],
  worker: [
    '安装位置', '监控方向', '光缆布放', '光功率测试', '电缆施工', '取电位置', '箱体通电',
    '立杆方式', '支臂安装', '摄像头安装', '摄像头序列号', '防雨箱安装', 'PING测试记录', '光猫编号'
  ],
  maintenance: ['故障处理']
};

var BUILD_EXTRA_FIELDS = {
  '安装位置': [
    { key: 'pole_mount_type', label: '立杆方式', type: 'select', options: ['立杆3.5米', '立杆6米', '借杆', '借墙'], required: true }
  ],
  '监控方向': [],
  '光缆布放': [
    { key: 'cable_aerial_m', label: '附挂（米）', placeholder: '请输入米数' },
    { key: 'cable_pipe_m', label: '管道（米）', placeholder: '请输入米数' },
    { key: 'cable_bridge_m', label: '桥架（米）', placeholder: '请输入米数' },
    { key: 'cable_buried_m', label: '直埋（米）', placeholder: '请输入米数' },
    { key: 'cable_work_note', label: '施工说明', placeholder: '选填，如熔接/成端/敷设' }
  ],
  '光功率测试': [
    { key: 'light_power_dbm', label: '光功率（dBm）', placeholder: '选填，如 -18.5' },
    { key: 'port_info', label: '端口信息', placeholder: '如 GE1/0/1' }
  ],
  '电缆施工': [
    { key: 'cable_overhead_m', label: '电缆架空（米）', placeholder: '请输入米数' },
    { key: 'dig_soil_m', label: '开挖土路（米）', placeholder: '请输入米数' },
    { key: 'dig_cement_m', label: '开挖水泥路面（米）', placeholder: '请输入米数' }
  ],
  '取电位置': [
    {
      key: 'power_status',
      label: '取电情况',
      type: 'select',
      options: ['国网电表取电', '交警箱取电', '天网箱取电', '低压箱取电', '其它'],
      required: true
    }
  ],
  '箱体通电': [
    { key: 'power_on_note', label: '通电情况说明', placeholder: '选填，如已通电/待验收' }
  ],
  '立杆方式': [
    { key: 'pole_type', label: '立杆方式', type: 'select', options: ['立杆3.5米', '立杆6米', '借杆', '借墙'], required: true }
  ],
  '支臂安装': [
    { key: 'arm_length_m', label: '支臂长度（米）', placeholder: '选填' }
  ],
  '摄像头安装': [
    {
      key: 'probe_type',
      label: '探头类型',
      type: 'select',
      options: ['海康双目枪机', '海康枪机', '海康球机', '大华枪机', '大华球机', '其它'],
      required: true
    }
  ],
  '摄像头序列号': [
    { key: 'camera_sn', label: '摄像头序列号', placeholder: '选填' }
  ],
  '防雨箱安装': [
    { key: 'rainproof_code', label: '防雨箱编码', placeholder: '选填' },
    { key: 'waterproof_code', label: '防水箱编码', placeholder: '选填' }
  ],
  'PING测试记录': [
    { key: 'broadband', label: '宽带账号', placeholder: '选填' },
    { key: 'device_ip', label: 'IP地址', placeholder: '必填', required: true }
  ],
  '光猫编号': [
    { key: 'ont_code', label: '光猫编号', placeholder: '选填' }
  ],
  '安全检查': [
    { key: 'work_env', label: '工作环境', type: 'select', options: ['常规环境', '带电环境', '封闭环境'], required: true }
  ],
  '设备离线检查': [],
  '故障处理': []
};

// 兼容旧数据中的施工内容名称
var BUILD_NAME_ALIASES = {
  '监控区域': '监控方向',
  '取电点开关': '电缆施工',
  '开挖路面': '电缆施工',
  '取电点位置': '取电位置',
  '点位箱体通电': '箱体通电',
  '杆件编码': '立杆方式',
  '支臂': '支臂安装',
  '防水箱': '防雨箱安装',
  '防雨箱': '防雨箱安装',
  '光缆': '光缆布放',
  '光缆敷设': '光缆布放',
  '布放光缆': '光缆布放',
  '光缆施工': '光缆布放',
  '光缆熔接': '光缆布放',
  '水箱安装': '箱体通电',
  '水箱': '箱体通电',
  '防水箱安装': '防雨箱安装',
  'PING测记录': 'PING测试记录'
};

function normalizeBuildName(name) {
  return BUILD_NAME_ALIASES[name] || name;
}

function initProjectSelect() {
  var sel = document.getElementById('wm_project');
  if (!sel) return;
  var cur = sel.value;
  var html = '<option value="">🏷️ 选择项目分类（必选）</option>';
  PROJECT_OPTIONS.forEach(function(p) {
    html += '<option value="' + p + '">' + p + '</option>';
  });
  sel.innerHTML = html;
  if (cur) sel.value = cur;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initProjectSelect);
} else {
  initProjectSelect();
}
