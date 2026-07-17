import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";

export function BackButton({ fallback, label }: { fallback: string; label: string }) {
  const navigate = useNavigate();

  function goBack() {
    const historyIndex = (window.history.state as { idx?: number } | null)?.idx;
    if (typeof historyIndex === "number" && historyIndex > 0) navigate(-1);
    else navigate(fallback);
  }

  return (
    <button className="back-link" type="button" onClick={goBack}>
      <ArrowLeft size={17} /> {label}
    </button>
  );
}
