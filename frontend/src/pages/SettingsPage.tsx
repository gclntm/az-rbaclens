import { Navigate, NavLink, Route, Routes } from "react-router-dom";

import { PageHeader } from "@/components/PageTemplate";
import { TabNav, tabClass } from "@/components/TabNav";
import AdviceTab from "@/pages/settings/AdviceTab";
import AuditTab from "@/pages/settings/AuditTab";
import AuthTab from "@/pages/settings/AuthTab";
import ChangelogTab from "@/pages/settings/ChangelogTab";
import CredentialsTab from "@/pages/settings/CredentialsTab";
import DatabaseTab from "@/pages/settings/DatabaseTab";
import DataKeptTab from "@/pages/settings/DataKeptTab";
import NotificationsTab from "@/pages/settings/NotificationsTab";
import ResponsibleTab from "@/pages/settings/ResponsibleTab";

const SECTIONS: [string, string][] = [
  ["responsible", "Who is responsible"],
  ["advice", "Advice"],
  ["notifications", "Notifications"],
  ["credentials", "Credentials"],
  ["authentication", "Authentication"],
  ["audit", "Audit log"],
  ["data-kept", "Data kept"],
  ["database", "Database"],
  ["changelog", "Changelog"],
];

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Settings"
        sub="Who is responsible for each place, how advice and notifications work, and how RBAC Lens itself runs."
      />
      <TabNav label="Settings sections">
        {SECTIONS.map(([path, label]) => (
          <NavLink key={path} to={`/settings/${path}`} className={({ isActive }) => tabClass(isActive)}>
            {label}
          </NavLink>
        ))}
      </TabNav>
      <Routes>
        <Route index element={<Navigate to="credentials" replace />} />
        <Route path="responsible" element={<ResponsibleTab />} />
        <Route path="advice" element={<AdviceTab />} />
        <Route path="notifications" element={<NotificationsTab />} />
        <Route path="credentials" element={<CredentialsTab />} />
        <Route path="authentication" element={<AuthTab />} />
        <Route path="audit" element={<AuditTab />} />
        <Route path="data-kept" element={<DataKeptTab />} />
        <Route path="database" element={<DatabaseTab />} />
        <Route path="changelog" element={<ChangelogTab />} />
      </Routes>
    </>
  );
}
