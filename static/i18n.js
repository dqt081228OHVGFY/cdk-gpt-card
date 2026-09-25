(() => {
  const STORAGE_KEY = "cdk-ui-language";
  const SUPPORTED = new Set(["zh", "en"]);

  const TEXT_EN = {
    "知道了": "Got it",
    "切换语言": "Switch language",
    "关闭提示": "Close notice",
    "关闭": "Close",
    "提示": "Notice",
    "操作完成": "Completed",
    "操作未完成": "Not completed",
    "生成成功": "Generated successfully",
    "生成失败": "Generation failed",
    "生成中": "Generating",
    "正在生成": "Generating",
    "正在查找": "Searching",
    "正在转换": "Converting",
    "正在验证": "Verifying",
    "进入管理控制台": "Enter console",
    "隐藏": "Hide",
    "显示": "Show",
    "隐藏密码": "Hide password",
    "显示密码": "Show password",
    "SUCCESS": "SUCCESS",
    "ERROR": "ERROR",
    "ACTION CHECK": "ACTION CHECK",
    "确认操作": "Confirm action",
    "确认": "Confirm",
    "取消": "Cancel",
    "仅下载": "Download only",
    "标记已使用": "Mark used",
    "下载选项": "Download options",
    "确认删除": "Confirm deletion",
    "确认永久删除": "Permanently delete",
    "确认追加": "Confirm add-on",
    "确认禁用": "Confirm disable",

    "GPT发卡网": "GPT CDK Store",
    "管理入口": "Admin",
    "CDK 兑换": "Redeem CDK",
    "格式转换": "Format convert",
    "查找": "Lookup",
    "领取操作": "Claim",
    "兑换 CDK": "Redeem CDK",
    "兑换码": "CDK",
    "已兑换过的兑换码": "Redeemed CDK",
    "自动识别 32 位十六进制码": "Auto-detects 32-char hex codes",
    "每次查找一个 32 位兑换码": "Look up one 32-character code at a time",
    "批量输入示例": "Batch input examples",
    "仅展示格式，每行一条即可批量兑换": "Format only. Paste one CDK per line for batch redemption.",
    "交付格式": "Delivery format",
    "目标格式": "Target format",
    "CPA JSON": "CPA JSON",
    "原始账号格式": "Original account format",
    "SUB JSON": "SUB JSON",
    "sub2api 格式": "sub2api format",
    "转为 SUB": "Convert to SUB",
    "合并账号格式": "Merged account format",
    "转为 CPA": "Convert to CPA",
    "独立账号格式": "Single-account format",
    "生成临时下载链接": "Generate temporary download link",
    "开始转换并生成链接": "Convert and generate link",
    "查找原文件": "Find original file",
    "兑换行为将记录时间与使用状态": "Redemption time and usage status will be recorded",
    "转换完成后生成一次性下载链接": "A one-time download link is generated after conversion",
    "查找不会更换卡密原先绑定的文件": "Lookup will not change the file originally bound to this CDK",
    "文件已就绪": "File ready",
    "转换完成": "Converted",
    "原文件已找回": "Original file found",
    "下载文件": "Download file",
    "下载转换文件": "Download converted file",
    "下载原文件": "Download original file",
    "一次性链接，下载后立即失效": "One-time link, expires immediately after download",
    "输入兑换码，": "Enter your CDK,",
    "领取专属文件。": "claim your file.",
    "识别原始格式，": "Detect the source format,",
    "转换即刻完成。": "convert instantly.",
    "忘记账号？": "Lost the account?",
    "原文件还能找回。": "Find the original file.",
    "一户一码安全交付。单个 CDK 输出 JSON，多条 CDK 打包 ZIP，下载后链接立即失效。": "One account per CDK. A single CDK returns JSON; multiple CDKs are packed as ZIP; links expire after download.",
    "上传原始 JSON、CPA JSON、SUB JSON 或 ZIP。系统自动识别源格式，并按你的选择生成 CPA 或 SUB 文件。": "Upload raw JSON, CPA JSON, SUB JSON, or ZIP. The system detects the source format and exports CPA or SUB as selected.",
    "兑换后不小心关了页面、没抄到账号？输入原兑换码在这里重新查回。": "Closed the page or missed the account after redemption? Enter the original CDK to retrieve it here.",
    "当前账号库存": "Current inventory",
    "当前可用库存": "Available inventory",
    "当前交付状态": "Delivery status",
    "实时可交付商品": "Live deliverable products",
    "库存充足": "In stock",
    "库存告急": "Low stock",
    "无库存": "Out of stock",
    "已上架": "Listed",
    "可用库存": "Available",
    "在售 CDK": "CDKs on sale",
    "暂无可提货商品": "No products are currently available",
    "交付保障": "Delivery safeguards",
    "一户一码": "One account per CDK",
    "独立账号绑定": "Independent account binding",
    "防爆破": "Anti-bruteforce",
    "异常请求限制": "Abnormal request throttling",
    "临时链接": "Temporary links",
    "到期自动清理": "Auto cleanup",
    "查找说明": "Lookup notes",
    "原码反查": "Original-code lookup",
    "无需再次消耗次数": "No extra usage consumed",
    "原文件": "Original file",
    "不更换绑定内容": "Binding stays unchanged",
    "JSON / CPA / SUB / SUB2 / ZIP 文件": "JSON / CPA / SUB / SUB2 / ZIP files",
    "拖放文件到这里，或点击选择": "Drop files here or click to choose",
    "支持 JSON、CPA、SUB、SUB2、ZIP": "Supports JSON, CPA, SUB, SUB2, ZIP",

    "正在准备提货任务": "Preparing delivery",
    "交付检查": "Delivery check",
    "提货进度": "Delivery progress",
    "验码": "Validate",
    "封装": "Package",
    "完成": "Complete",
    "正在校验兑换码并排队": "Validating CDKs and queuing the request",
    "正在生成交付文件": "Building delivery file",
    "库存确认后生成一次性下载链接": "A one-time link is created after the inventory is confirmed",
    "正在收尾": "Finalizing",
    "加密库存正在安全出库": "Encrypted inventory is being safely checked out",
    "提货任务完成": "Delivery completed",
    "提货任务未完成": "Delivery failed",
    "下载链接已生成，请及时保存": "Download link generated. Please save it soon",
    "请根据提示调整后重试": "Adjust based on the prompt and try again",
    "正在按商品库存打包交付文件，请稍候": "Packing delivery files from product inventory, please wait",

    "控制台": "Dashboard",
    "管理员管理": "Admins",
    "商品管理": "Products",
    "CDK 管理": "CDK management",
    "账号文件": "Account files",
    "批量导入": "Bulk import",
    "账户设置": "Account settings",
    "退出管理": "Log out",
    "超级管理": "Super admin",
    "管理员": "Admin",
    "一户一码生成、状态控制与兑换反查": "Generate one-account CDKs, manage status, and inspect redemptions",
    "查看识别格式、绑定 CDK 与交付状态": "View detected format, bound CDK, and delivery status",
    "搜索卡密": "Search CDK",
    "搜索文件名": "Search filename",
    "卡密精确搜索": "Exact CDK search",
    "商品筛选": "Product filter",
    "CDK 状态": "CDK status",
    "文件状态": "File status",
    "目标状态": "Target status",
    "开始日期": "Start date",
    "结束日期": "End date",
    "全部商品": "All products",
    "全部状态": "All statuses",
    "可用": "Available",
    "不可用": "Unavailable",
    "筛选": "Filter",
    "重置": "Reset",
    "批量修改状态": "Batch status",
    "批量下载": "Batch download",
    "使用策略": "Usage policy",
    "追加 1 次兑换": "Add 1 redemption",
    "批量禁用": "Batch disable",
    "批量删除": "Batch delete",
    "批量删除账号": "Batch delete accounts",
    "生成卡密": "Generate CDKs",
    "序号": "No.",
    "卡密": "CDK",
    "商品": "Product",
    "账号数": "Accounts",
    "使用次数": "Usage",
    "过期时间": "Expiry",
    "状态": "Status",
    "首次使用时间": "First use",
    "作废时间": "Void time",
    "创建时间": "Created",
    "操作用户": "Operator",
    "操作": "Actions",
    "文件名": "Filename",
    "识别格式": "Detected format",
    "账号邮箱": "Account email",
    "首次提取时间": "First delivery",
    "关联卡密": "Bound CDK",
    "上传时间": "Uploaded",
    "复制": "Copy",
    "复制卡密": "Copy CDK",
    "复制兑换码": "Copy CDK",
    "使用详情": "Usage details",
    "暂无卡密": "No CDKs",
    "暂无文件": "No files",
    "暂无账号文件": "No account files",
    "上一页": "Previous",
    "下一页": "Next",
    "每页显示数量": "Items per page",
    "生成 CDK": "Generate CDK",
    "每个 CDK 固定绑定一个账号": "Each CDK is bound to one account",
    "账号": "Account",
    "所属商品": "Product",
    "生成数量": "Quantity",
    "每码最大使用次数": "Max uses per CDK",
    "留空表示永不过期": "Leave blank for no expiry",
    "批量设置使用策略": "Batch usage policy",
    "尚未选择卡密": "No CDKs selected",
    "留空表示永不过期；不能早于当前时间": "Leave blank for no expiry; must not be earlier than now",
    "保存使用策略": "Save policy",
    "兑换次数": "Redemptions",
    "最大使用次数": "Max uses",
    "永不过期": "Never expires",
    "暂无使用记录": "No usage records",
    "本次生成的兑换码": "Generated CDKs",
    "全部复制": "Copy all",
    "下载": "Download",

    "可使用": "Pending",
    "已使用": "Used",
    "已作废": "Voided",
    "已过期": "Expired",
    "已提取": "Delivered",
    "锁定中": "Locked",
    "系统": "System",
    "全部": "All",
    "下载后可选择是否同时标记这些账号已使用。": "After downloading, choose whether to also mark these accounts as used.",
    "确定为选中的卡密追加 1 次兑换？追加后仍使用原卡密领取原绑定文件。": "Add 1 redemption to the selected CDKs? They will still use the original CDKs to claim the originally bound files.",
    "确定禁用选中的卡密？禁用后将立即无法兑换。": "Disable the selected CDKs? They will no longer be redeemable immediately.",
    "确定删除选中的卡密？所有状态均会删除，相关兑换记录和临时链接也会一并移除，此操作不可恢复。": "Delete the selected CDKs? All statuses will be removed, related redemption records and temporary links will also be deleted. This cannot be undone.",
    "确定永久删除选中的账号文件？此操作不可恢复；已交付或已兑换关联的账号会被拒绝删除。": "Permanently delete the selected account files? This cannot be undone; delivered or redemption-bound accounts will be refused.",

    "未识别到有效的 32 位 CDK": "No valid 32-character CDK detected",
    "每次只能查找一个已兑换的 CDK": "You can look up only one redeemed CDK at a time",
    "网络超时，请稍后重试": "Network timed out. Please try again later",
    "登录状态已失效，即将重新进入": "Your session expired. Redirecting back in",
    "服务器返回异常": "Unexpected server response",
    "请求失败": "Request failed",
    "下载链接无效": "Invalid download link",
    "交付链接已生成": "Delivery link generated",
    "格式转换完成": "Format conversion completed",
    "转换失败": "Conversion failed",
    "兑换失败": "Redemption failed",
    "查找失败": "Lookup failed",
    "请选择 JSON、CPA、SUB、SUB2 或 ZIP 文件": "Choose JSON, CPA, SUB, SUB2, or ZIP files",
    "下载失败，请刷新后重试": "Download failed. Refresh and try again",
    "接口返回异常，请刷新页面后重试": "Unexpected API response. Refresh and try again",
    "修改状态失败": "Failed to update status",
    "状态已修改": "Status updated",
    "卡密已复制": "CDK copied",
    "复制失败，请手动复制": "Copy failed. Please copy manually",
    "已复制": "Copied",
    "已下载": "Downloaded",
    "加载中...": "Loading...",
    "使用详情加载失败": "Failed to load usage details",
    "生成下载链接": "Generate download link",
    "下载文件 · 一次性": "Download file · one-time",
    "重新生成": "Regenerate",
    "一次性下载链接已生成，下载后立即失效": "One-time download link generated. It expires immediately after download.",
    "生成下载链接失败": "Failed to generate download link",
    "切换失败": "Toggle failed",
    "服务器返回异常，请刷新页面后重试": "Unexpected server response. Refresh and try again",
    "生成卡密失败": "Failed to generate CDKs",
    "卡密已生成，但服务器未返回生成结果": "CDKs were generated, but the server did not return the result",
    "复制失败，请逐条复制": "Copy failed. Please copy one by one",
    "选择 JSON / CPA / SUB / SUB2 / ZIP": "Choose JSON / CPA / SUB / SUB2 / ZIP",
    "尚未选择文件": "No files selected",
    "只能上传 JSON、CPA、SUB、SUB2 或 ZIP 文件": "Only JSON, CPA, SUB, SUB2, or ZIP files are allowed",

    "运行概览": "Operations overview",
    "账号库存、CDK 交付与临时下载状态": "Account inventory, CDK delivery, and temporary download status",
    "今日流转": "Today's flow",
    "最近操作": "Recent activity",
    "暂无操作记录": "No activity records",
    "今日上传": "Uploaded today",
    "昨日上传": "Uploaded yesterday",
    "今日交付": "Delivered today",
    "昨日交付": "Delivered yesterday",
    "今日兑换": "Redeemed today",
    "有效临时链接": "Active temporary links",
    "累计上传": "Total uploaded",
    "累计交付": "Total delivered",
    "可用 CDK": "Available CDKs",

    "GPT发卡网首页": "GPT CDK Store home",
    "主导航": "Main navigation",
    "进入管理员登录页面": "Go to admin login",
    "CDK 输入示例": "CDK input examples",
    "返回管理控制台": "Back to console",

    "返回 GPT发卡网": "Back to GPT CDK Store",
    "返回兑换页": "Back to redemption",
    "管理你的": "Manage your",
    "CDK 交付系统。": "CDK delivery system.",
    "生成一户一码 CDK、批量导入账号文件，并反查每一次兑换和临时下载记录。": "Generate one-account CDKs, bulk-import account files, and trace every redemption and temporary download.",
    "管理端功能": "Admin features",
    "CDK 生成": "CDK generation",
    "使用反查": "Usage lookup",
    "链接重建": "Link rebuild",
    "控制台访问": "Console access",
    "管理员登录": "Admin login",
    "管理员账号": "Admin username",
    "登录密码": "Password",
    "登录失败次数过多将被临时限制": "Too many failed logins will be temporarily blocked",

    "下载后立即失效": "Expires immediately after download",
    "反查操作": "Reverse lookup",
    "查找已兑换文件": "Find redeemed file",

    "支持原始 JSON 转 CPA 或 SUB、CPA 转 SUB、SUB 转 CPA": "Supports raw JSON to CPA or SUB, CPA to SUB, and SUB to CPA",
    "原始 JSON": "Raw JSON",
    "转换操作": "Convert",
    "上传源文件": "Upload source file",

    "创建和维护普通管理账号；超级管理账号受保护": "Create and manage standard admin accounts; super admin accounts are protected",
    "创建普通管理": "Create standard admin",
    "角色": "Role",
    "号池地址": "Quota pool URL",
    "更新时间": "Updated",
    "不能禁用当前登录账号": "Cannot disable the currently logged-in account",
    "受保护": "Protected",
    "重置密码": "Reset password",
    "配置号池": "Configure quota pool",
    "配置文件管理": "Managed via config file",
    "普通管理不能查看或修改超级管理账号": "Standard admins cannot view or modify super admin accounts",
    "密码": "Password",
    "管理秘钥": "Management key",
    "为": "For",
    "设置检测用号池": "Set the detection quota pool",
    "留空则不修改": "Leave blank to keep unchanged",
    "保存配置": "Save configuration",
    "设置新密码": "Set new password",
    "新密码": "New password",
    "确认重置": "Confirm reset",

    "修改当前管理账号的登录密码": "Change the current admin account's password",
    "当前密码": "Current password",
    "确认新密码": "Confirm new password",
    "更新密码并退出": "Update password and log out",

    "自动识别 CPA、SUB 与账号数组，SUB 多账号会拆分为独立库存": "Automatically detects CPA, SUB, and account arrays; multi-account SUB is split into separate inventory",
    "导入方式": "Import method",
    "本地文件": "Local files",
    "手动输入": "Manual input",
    "账号 JSON 入库": "Account JSON import",
    "支持 JSON、CPA、SUB、SUB2 或包含这些文件的 ZIP。": "Supports JSON, CPA, SUB, SUB2, or a ZIP containing these files.",
    "单批最大 500MB": "Up to 500MB per batch",
    "单批上限": "Batch limit",
    "文件": "File",
    "上传要求": "Upload requirements",
    "独立入库": "Imported individually",
    "自动拆分": "Auto split",
    "大小": "Size",
    "不超过 500MB / 500 个账号": "Up to 500MB / 500 accounts",
    "识别结果可在账号文件页查看；普通 JSON 会以 UNKNOWN 标记入库": "Detection results are available on the account files page; plain JSON is imported as UNKNOWN",
    "上传": "Upload",
    "手动 JSON 入库": "Manual JSON import",
    "粘贴单个账号、账号数组或 SUB 配置。": "Paste a single account, an account array, or a SUB config.",
    "手动输入最大 2MB": "Up to 2MB for manual input",
    "输入上限": "Input limit",
    "JSON 内容": "JSON content",
    "识别并导入": "Detect and import",

    "全选本页 CDK": "Select all CDKs on this page",
    "关闭生成结果": "Close generation result",
    "全选本页文件": "Select all files on this page",

    "每个商品独立绑定库存与 CDK。": "Each product has its own inventory and CDKs.",
    "新建商品": "New product",
    "商品名称": "Product name",
    "描述": "Description",
    "低库存阈值": "Low-stock threshold",
    "创建商品": "Create product",
    "交付规则": "Delivery rules",
    "库存交付": "Inventory delivery",
    "直接从商品可用库存中发货": "Ships directly from the product's available inventory",
    "低库存": "Low stock",
    "低于阈值时首页标记库存告急": "Marks low stock on the homepage when below the threshold",
    "商品隐藏": "Product hidden",
    "已发 CDK 也不会继续兑换": "Issued CDKs can no longer be redeemed",
    "商品级库存隔离": "Product-level inventory isolation",
    "不提供删除商品，避免破坏历史兑换记录。": "Products cannot be deleted, to preserve historical redemption records.",
    "可交付": "Deliverable",
    "兑换": "Redemptions",
    "编辑": "Edit",
    "保存": "Save",
    "暂无商品": "No products",

    "启用": "Enabled",
    "禁用": "Disabled",
    "普通管理": "Standard admin",
    "历史账号": "Legacy account",
    "草稿": "Draft",
    "已隐藏": "Hidden",
    "待使用": "Pending use",

    "管理中心": "Control center",
    "卡密管理": "CDK management",
    "文件管理": "File management",
    "上传文件": "Upload files"
  };

  const KEY_EN = {
    lang_toggle: "EN",
    lang_toggle_title: "Switch language",
    lang_state: "English",
    lang_switch_to: "中文",
    notice_success_title: "Generated successfully",
    notice_success_kicker: "CDK READY",
    notice_error_title: "Not completed",
    notice_error_kicker: "ACTION BLOCKED",
    notice_confirm: "Got it",
    selected_items: (count) => `Selected ${count}`,
    code_count: (count) => `${count} valid code${count === 1 ? "" : "s"} detected`,
    max_redeem: (count) => `Up to ${count} CDKs per redemption. Please split the batch.`,
    uploaded_files: (count, size) => `${count} file${count === 1 ? "" : "s"} · ${size}`,
    uploaded_more: (count) => `${count} more file${count === 1 ? "" : "s"}`,
    selected_files: (count, size) => `Selected ${count} file${count === 1 ? "" : "s"}, ${size} total`,
    selected_files_count: (count) => `${count} file${count === 1 ? "" : "s"} selected`,
    generated_codes: (count) => `${count} CDK${count === 1 ? "" : "s"} created`,
    generated_count: (count) => `${count} item${count === 1 ? "" : "s"}`,
    policy_selected: (count) => `Updating ${count} CDK${count === 1 ? "" : "s"}`,
    usage_item: (index, count) => `Use #${index} · ${count} file${Number(count) === 1 ? "" : "s"}`,
  };

  const PATTERNS_EN = [
    [/^已选\s*(\d+)\s*个$/, (_m, count) => `Selected ${count}`],
    [/^识别到\s*(\d+)\s*个有效兑换码$/, (_m, count) => `${count} valid code${Number(count) === 1 ? "" : "s"} detected`],
    [/^单次最多兑换\s*(\d+)\s*个 CDK，请分批处理$/, (_m, count) => `Up to ${count} CDKs per redemption. Please split the batch.`],
    [/^已创建\s*(\d+)\s*个兑换码$/, (_m, count) => `${count} CDK${Number(count) === 1 ? "" : "s"} created`],
    [/^已复制\s*(\d+)\s*个兑换码$/, (_m, count) => `${count} CDK${Number(count) === 1 ? "" : "s"} copied`],
    [/^本次生成的兑换码$/, () => "Generated CDKs"],
    [/^(\d+)\s*个$/, (_m, count) => `${count} item${Number(count) === 1 ? "" : "s"}`],
    [/^将更新\s*(\d+)\s*张卡密$/, (_m, count) => `Updating ${count} CDK${Number(count) === 1 ? "" : "s"}`],
    [/^第\s*(\d+)\s*次使用 ·\s*(\d+)\s*个文件$/, (_m, index, count) => `Use #${index} · ${count} file${Number(count) === 1 ? "" : "s"}`],
    [/^另有\s*(\d+)\s*个文件$/, (_m, count) => `${count} more file${Number(count) === 1 ? "" : "s"}`],
    [/^已选择\s*(\d+)\s*个文件$/, (_m, count) => `${count} file${Number(count) === 1 ? "" : "s"} selected`],
    [/^已选择\s*(\d+)\s*个文件，共\s*(.+)$/, (_m, count, size) => `Selected ${count} file${Number(count) === 1 ? "" : "s"}, ${size} total`],
    [/^当前选择\s*(\d+)\s*个文件，共\s*(.+)，已超过 500MB$/, (_m, count, size) => `Selected ${count} file${Number(count) === 1 ? "" : "s"}, ${size} total, over 500MB`],
    [/^单批最多选择\s*(\d+)\s*个文件$/, (_m, count) => `Up to ${count} files per batch`],
    [/^文件名不能包含中文，将跳过：(.+)$/, (_m, name) => `Filenames cannot contain Chinese characters; skipped: ${name}`],
    [/^共\s*(\d+)\s*条，第\s*(\d+)\s*\/\s*(\d+)\s*页$/, (_m, total, page, pages) => `${total} total · Page ${page} / ${pages}`],
    [/^每页\s*(\d+)$/, (_m, size) => `${size} / page`],
    [/^账号\s*(.+)\s*已(.+)$/, (_m, username, state) => `Account ${username} is now ${translateCore(state).toLowerCase()}`],
    [/^正在生成$/, () => "Generating"],
    [/^选择 CDK (.+)$/, (_m, code) => `Select CDK ${code}`],
    [/^选择文件 (.+)$/, (_m, name) => `Select file ${name}`],
    [/^(.+?)\s*·\s*GPT发卡网$/, (_m, head) => `${translateCore(head)} · GPT CDK Store`],
    [/^(\d{1,2})\s*\/\s*(\D.*)$/, (_m, num, label) => `${num} / ${translateCore(label)}`],
  ];

  const textOriginals = new WeakMap();
  const attrOriginals = new WeakMap();
  let currentLang = readInitialLanguage();
  let observer = null;
  let applying = false;
  let originalTitle = null;

  function readInitialLanguage() {
    try {
      const queryLang = new URLSearchParams(window.location.search).get("lang");
      if (SUPPORTED.has(queryLang)) {
        window.localStorage?.setItem(STORAGE_KEY, queryLang);
        return queryLang;
      }
      const saved = window.localStorage?.getItem(STORAGE_KEY);
      if (SUPPORTED.has(saved)) return saved;
    } catch (_) {}
    return "zh";
  }

  function translateCore(value) {
    const source = String(value ?? "");
    if (currentLang !== "en") return source;
    if (Object.prototype.hasOwnProperty.call(TEXT_EN, source)) return TEXT_EN[source];
    for (const [pattern, replacer] of PATTERNS_EN) {
      const match = source.match(pattern);
      if (match) return replacer(...match);
    }
    return source;
  }

  function translateWithWhitespace(value) {
    const source = String(value ?? "");
    const core = source.trim();
    if (!core) return source;
    const leading = source.match(/^\s*/)?.[0] || "";
    const trailing = source.match(/\s*$/)?.[0] || "";
    return `${leading}${translateCore(core)}${trailing}`;
  }

  function translateText(value) {
    return translateWithWhitespace(String(value ?? ""));
  }

  function translateKey(key, fallback = "") {
    const value = currentLang === "en" ? KEY_EN[key] : "";
    if (typeof value === "function") return fallback;
    return value || translateText(fallback);
  }

  function formatKey(key, fallback, ...args) {
    const value = currentLang === "en" ? KEY_EN[key] : null;
    if (typeof value === "function") return value(...args);
    if (typeof value === "string") return value;
    return translateText(fallback);
  }

  function shouldSkipElement(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return false;
    if (element.closest?.("[data-i18n-skip]")) return true;
    return ["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA"].includes(element.tagName);
  }

  function applyTextNode(node) {
    const parent = node.parentElement;
    if (shouldSkipElement(parent)) return;
    if (!textOriginals.has(node)) textOriginals.set(node, node.nodeValue);
    const original = textOriginals.get(node);
    const next = currentLang === "zh" ? original : translateWithWhitespace(original);
    if (node.nodeValue !== next) node.nodeValue = next;
  }

  function originalAttrMap(element) {
    let map = attrOriginals.get(element);
    if (!map) {
      map = new Map();
      attrOriginals.set(element, map);
    }
    return map;
  }

  function applyAttribute(element, attr) {
    if (!element.hasAttribute(attr)) return;
    const map = originalAttrMap(element);
    if (!map.has(attr)) map.set(attr, element.getAttribute(attr));
    const original = map.get(attr);
    const next = currentLang === "zh" ? original : translateWithWhitespace(original);
    if (element.getAttribute(attr) !== next) element.setAttribute(attr, next);
  }

  function applyExplicitElement(element) {
    const key = element.dataset?.i18n;
    if (!key) return;
    const fallback = element.dataset.i18nFallback || element.textContent;
    const next = translateKey(key, fallback);
    if (element.textContent !== next) element.textContent = next;
  }

  function applyTranslations(root = document.body) {
    if (!root || applying) return;
    applying = true;
    try {
      const target = root.nodeType === Node.ELEMENT_NODE ? root : document.body;
      if (!target) return;

      if (target.nodeType === Node.ELEMENT_NODE && !shouldSkipElement(target)) {
        applyExplicitElement(target);
        ["placeholder", "title", "aria-label"].forEach((attr) => applyAttribute(target, attr));
      }

      const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
        acceptNode(node) {
          if (node.nodeType === Node.ELEMENT_NODE && shouldSkipElement(node)) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      });

      let node = walker.nextNode();
      while (node) {
        if (node.nodeType === Node.TEXT_NODE) {
          applyTextNode(node);
        } else if (node.nodeType === Node.ELEMENT_NODE) {
          applyExplicitElement(node);
          ["placeholder", "title", "aria-label"].forEach((attr) => applyAttribute(node, attr));
        }
        node = walker.nextNode();
      }
    } finally {
      applying = false;
    }
  }

  function updateDocumentLanguage() {
    document.documentElement.lang = currentLang === "en" ? "en" : "zh-CN";
    document.documentElement.dataset.lang = currentLang;
  }

  function applyDocumentTitle() {
    // <title> lives in <head>, which applyTranslations() never walks, so it is
    // translated here. The original (Chinese) title is captured once so a switch
    // back to zh restores it verbatim.
    if (originalTitle === null) originalTitle = document.title;
    const next = currentLang === "zh" ? originalTitle : translateWithWhitespace(originalTitle);
    if (document.title !== next) document.title = next;
  }

  function animateLanguageSwitch() {
    const root = document.documentElement;
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!root || reducedMotion?.matches) return;
    // Compositor-only settle on the content column: one opacity + lift pass on
    // <main>, no per-element overlay clones and no clip-path/blur, so it stays
    // perfectly fluid. Re-adding the class after a forced reflow restarts it.
    root.classList.remove("i18n-swapping");
    void root.offsetWidth;
    root.classList.add("i18n-swapping");
    window.clearTimeout(animateLanguageSwitch._timer);
    animateLanguageSwitch._timer = window.setTimeout(
      () => root.classList.remove("i18n-swapping"),
      520,
    );
  }

  function updateToggle(button) {
    if (!button) return;
    const isEnglish = currentLang === "en";
    button.setAttribute("aria-pressed", String(isEnglish));
    button.setAttribute("title", translateKey("lang_toggle_title", "切换语言"));
    button.setAttribute("aria-label", translateKey("lang_toggle_title", "切换语言"));
    button.innerHTML = `
      <span>${isEnglish ? "EN" : "中"}</span>
      <b>${isEnglish ? "中文" : "EN"}</b>
    `;
  }

  function ensureToggle() {
    let button = document.querySelector("[data-language-toggle]");
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "language-switcher";
      button.dataset.languageToggle = "";
      button.setAttribute("aria-label", "切换语言");
      button.addEventListener("click", () => setLanguage(currentLang === "en" ? "zh" : "en"));
      document.body.appendChild(button);
    }
    updateToggle(button);
    return button;
  }

  function setLanguage(lang) {
    if (!SUPPORTED.has(lang)) return;
    const changed = currentLang !== lang;
    if (changed) animateLanguageSwitch();
    currentLang = lang;
    try {
      window.localStorage?.setItem(STORAGE_KEY, lang);
    } catch (_) {}
    updateDocumentLanguage();
    applyTranslations(document.body);
    applyDocumentTitle();
    const toggle = ensureToggle();
    if (changed && toggle) {
      toggle.classList.remove("is-switching");
      void toggle.offsetWidth;
      toggle.classList.add("is-switching");
      window.setTimeout(() => toggle.classList.remove("is-switching"), 580);
    }
  }

  function observeMutations() {
    if (observer || !document.body) return;
    observer = new MutationObserver((mutations) => {
      if (applying) return;
      for (const mutation of mutations) {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) applyTranslations(node);
          if (node.nodeType === Node.TEXT_NODE) applyTextNode(node);
        });
        if (mutation.type === "characterData") applyTextNode(mutation.target);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function init() {
    updateDocumentLanguage();
    applyTranslations(document.body);
    applyDocumentTitle();
    ensureToggle();
    observeMutations();
  }

  window.CDKI18N = {
    get language() {
      return currentLang;
    },
    setLanguage,
    text: translateText,
    t: translateKey,
    format: formatKey,
    apply: applyTranslations,
  };

  updateDocumentLanguage();
  if (document.body) applyTranslations(document.body);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
