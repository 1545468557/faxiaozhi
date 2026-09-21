import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

/** 占位页：说清这个入口将来做什么，并给一条现在就能走通的路。 */
export function PlaceholderPage({
  icon,
  title,
  lead,
  points,
  note,
}: {
  icon: ReactNode;
  title: string;
  lead: string;
  points: string[];
  note: string;
}) {
  return (
    <div className="v2-placeholder">
      <div className="v2-placeholder-icon" aria-hidden="true">
        {icon}
      </div>
      <h1>{title}</h1>
      <p>{lead}</p>
      <ul>
        {points.map(item => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p className="v2-note" style={{ marginTop: 18 }}>
        {note}
      </p>
      <Link className="v2-back-link" href="/">
        <ArrowLeft size={15} aria-hidden="true" />
        去法律问答提问
      </Link>
    </div>
  );
}
