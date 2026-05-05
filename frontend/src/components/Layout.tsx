import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "放映厅", end: true },
  { to: "/library", label: "媒资库" },
  { to: "/upload", label: "上传切分" },
  { to: "/jobs", label: "干预任务" },
  { to: "/config", label: "模型配置" },
];

export default function Layout() {
  return (
    <div className="min-h-dvh flex flex-col bg-[#09090b]">
      <header className="border-b border-zinc-800/90 bg-zinc-950/90 backdrop-blur-md sticky top-0 z-30">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3 flex items-center gap-4 flex-wrap">
          <NavLink to="/" className="group flex flex-col shrink-0">
            <span className="text-lg font-semibold tracking-tight text-zinc-50 group-hover:text-sky-100 transition-colors">
              叙境
            </span>
            <span className="text-[10px] tracking-wide text-zinc-500 uppercase">
              Narrative Immersion
            </span>
          </NavLink>
          <nav className="flex items-center gap-1 text-sm flex-wrap">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  `px-3 py-1.5 rounded-lg transition-colors ${
                    isActive
                      ? "bg-sky-500/15 text-sky-300"
                      : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/80"
                  }`
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          <p className="ml-auto text-[11px] text-zinc-600 max-sm:w-full max-sm:ml-0">
            AI 交互叙事 · 长视频沉浸体验
          </p>
        </div>
      </header>
      <main className="flex-1 flex flex-col min-h-0">
        <Outlet />
      </main>
      <footer className="border-t border-zinc-800/90 text-[11px] text-zinc-600 py-4 text-center px-4">
        叙境 · 赛题演示 · 成片理解 / 对话 / 叙事分支 · 后台保留完整编排与质检链路
      </footer>
    </div>
  );
}
