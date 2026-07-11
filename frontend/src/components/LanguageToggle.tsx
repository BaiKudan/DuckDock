import { Languages } from "lucide-react";
import { useI18n } from "../i18n";

export default function LanguageToggle() {
  const { locale, setLocale, t } = useI18n();

  return (
    <div className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white p-1 shadow-sm">
      <div className="flex h-8 w-8 items-center justify-center text-slate-400">
        <Languages className="h-4 w-4" />
      </div>
      <button
        onClick={() => setLocale("zh")}
        className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
          locale === "zh" ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50 hover:text-slate-700"
        }`}
      >
        {t("lang.zh")}
      </button>
      <button
        onClick={() => setLocale("en")}
        className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
          locale === "en" ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50 hover:text-slate-700"
        }`}
      >
        {t("lang.en")}
      </button>
    </div>
  );
}
