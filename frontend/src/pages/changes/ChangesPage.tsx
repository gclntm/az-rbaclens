import { useEffect, useState } from "react";

import { api, type ChangesResponse } from "@/api/client";
import { CAPTION } from "@/components/classes";
import { PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";

import ChangesFeed from "./ChangesFeed";

/** Who got or lost access, when, and who did it — the whole tenant. Route: /changes. */
export default function ChangesPage() {
  const [month, setMonth] = useState<ChangesResponse | null>(null);

  useEffect(() => {
    api.changes
      .list({ days: 30, limit: 1 })
      .then(setMonth)
      .catch(() => setMonth(null));
  }, []);

  return (
    <>
      <PageHeader
        title="Changes"
        sub="Who got or lost access, when, and who did it, across the whole tenant."
      />

      {month && (
        <StatusTiles>
          <StatusTile
            tone="info"
            icon="changes"
            label="Given, last 30 days"
            value={`+${month.given.toLocaleString()}`}
            note={month.given === 1 ? "1 grant" : `${month.given.toLocaleString()} grants`}
          />
          <StatusTile
            tone="info"
            icon="changes"
            label="Removed, last 30 days"
            value={`−${month.removed.toLocaleString()}`}
            note={month.removed === 1 ? "1 grant" : `${month.removed.toLocaleString()} grants`}
          />
          <StatusTile
            tone={month.powerful > 0 ? "warn" : "ok"}
            icon="shield"
            label="Powerful"
            value={month.powerful > 0 ? `${month.powerful.toLocaleString()} ${month.powerful === 1 ? "change" : "changes"}` : "None"}
            note="Can change who has access, or get keys"
          />
        </StatusTiles>
      )}

      <SectionCard icon="changes" title="Timeline" hint="Grouped by day, newest first. Pick a period, a direction, or only the powerful ones.">
        <ChangesFeed chart summary />
      </SectionCard>

      <SectionCard icon="history" title="Where changes come from">
        <p className={CAPTION} style={{ margin: 0 }}>
          The Azure Activity Log says who made a change and exactly when; RBAC Lens collects it every hour and keeps
          it for 120 days. The daily check catches everything else (group membership, PIM, access given on
          management groups) and shows when it was first seen. For a new grant, Azure also records who created it and
          when. Who added someone to a group, or removed them, comes from the Entra ID audit log once RBAC Lens can
          read it (the Microsoft Graph permission AuditLog.Read.All); a whole group nested into another stays "first
          seen".
        </p>
      </SectionCard>
    </>
  );
}
