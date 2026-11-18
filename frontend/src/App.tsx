import { lazy, Suspense } from "react";
import { RouterProvider } from "react-aria-components";
import { Navigate, Route, Routes, useHref, useLocation, useNavigate } from "react-router-dom";

import AuthProvider from "@/auth/AuthProvider";
import CommandPalette from "@/components/CommandPalette";
import { ConfirmProvider } from "@/components/ConfirmDialog";
import ErrorBoundary from "@/components/ErrorBoundary";
import { GroupMembersProvider } from "@/components/GroupMembersDrawer";
import OpenAccessBanner from "@/components/OpenAccessBanner";
import { RoleDetailProvider } from "@/components/RoleDetailDrawer";
import SideMenu from "@/components/SideMenu";

// Each page is its own file, loaded when first opened (docs/design.md).
const ChangesPage = lazy(() => import("@/pages/changes/ChangesPage"));
const EventsPage = lazy(() => import("@/pages/events/EventsPage"));
const FindingsPage = lazy(() => import("@/pages/findings/FindingsPage"));
const GrantPage = lazy(() => import("@/pages/grants/GrantPage"));
const HelpPage = lazy(() => import("@/pages/HelpPage"));
const JobsPage = lazy(() => import("@/pages/jobs/JobsPage"));
const IamAccessPage = lazy(() => import("@/pages/IamAccessPage"));
const NotFoundPage = lazy(() => import("@/pages/NotFoundPage"));
const OverviewPage = lazy(() => import("@/pages/overview/OverviewPage"));
const PrincipalPage = lazy(() => import("@/pages/principals/PrincipalPage"));
const PrincipalsIndexPage = lazy(() => import("@/pages/principals/PrincipalsIndexPage"));
const ReviewPage = lazy(() => import("@/pages/reviews/ReviewPage"));
const ReviewsPage = lazy(() => import("@/pages/reviews/ReviewsPage"));
const ReportPage = lazy(() => import("@/pages/report/ReportPage"));
const TodosPage = lazy(() => import("@/pages/todos/TodosPage"));
const RolesPage = lazy(() => import("@/pages/RolesPage"));
const ScopePage = lazy(() => import("@/pages/scopes/ScopePage"));
const ScopesIndexPage = lazy(() => import("@/pages/scopes/ScopesIndexPage"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));

/** Old addresses (/scopes/…, /principals/…) keep working for one release. */
function Moved({ from, to }: { from: string; to: string }) {
  const { pathname, search, hash } = useLocation();
  return <Navigate replace to={`${to}${pathname.slice(from.length)}${search}${hash}`} />;
}

/**
 * App-shell layout: the side menu (components/SideMenu) on the left.
 * Pages render directly under the main column without a
 * shared title bar — page-specific headers handle their own context.
 * RouterProvider lets Untitled UI's links (React Aria) navigate inside the
 * app instead of reloading the page.
 */
export default function App() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return (
    <RouterProvider navigate={navigate} useHref={useHref}>
    <ConfirmProvider>
    <AuthProvider>
      <RoleDetailProvider>
        <GroupMembersProvider>
          <div className="grid min-h-screen grid-cols-[240px_minmax(0,1fr)] max-[860px]:grid-cols-[minmax(0,1fr)]">
            <SideMenu />
            <main className="page-content grid grid-cols-[minmax(0,1fr)] content-start gap-6 px-8 py-6 max-[860px]:p-4 print:m-0! print:w-full! print:max-w-full! print:overflow-visible! print:p-0! print:[&>.card:first-of-type]:hidden">
              <OpenAccessBanner />
              <ErrorBoundary resetKey={pathname}>
              <Suspense fallback={<p className="my-[1em] text-quaternary">Loading…</p>}>
              <Routes>
                <Route path="/" element={<OverviewPage />} />
                <Route path="/places" element={<ScopesIndexPage />} />
                <Route path="/places/*" element={<ScopePage />} />
                <Route path="/people" element={<PrincipalsIndexPage />} />
                <Route path="/people/:principalId" element={<PrincipalPage />} />
                <Route path="/grants/:grantId" element={<GrantPage />} />
                <Route path="/findings" element={<FindingsPage />} />
                <Route path="/roles" element={<RolesPage />} />
                <Route path="/iam-access/*" element={<IamAccessPage />} />
                <Route path="/events" element={<EventsPage />} />
                <Route path="/changes" element={<ChangesPage />} />
                <Route path="/reviews" element={<ReviewsPage />} />
                <Route path="/reviews/:id" element={<ReviewPage />} />
                <Route path="/todos" element={<TodosPage />} />
                <Route path="/report" element={<ReportPage />} />
                <Route path="/help" element={<HelpPage />} />
                <Route path="/admin/data" element={<JobsPage />} />
                <Route path="/jobs" element={<Navigate replace to="/admin/data" />} />
                <Route path="/settings/*" element={<SettingsPage />} />
                <Route path="/scopes/*" element={<Moved from="/scopes" to="/places" />} />
                <Route path="/principals/*" element={<Moved from="/principals" to="/people" />} />
                <Route path="*" element={<NotFoundPage />} />
              </Routes>
              </Suspense>
              </ErrorBoundary>
            </main>
          </div>
          <CommandPalette />
        </GroupMembersProvider>
      </RoleDetailProvider>
    </AuthProvider>
    </ConfirmProvider>
    </RouterProvider>
  );
}
