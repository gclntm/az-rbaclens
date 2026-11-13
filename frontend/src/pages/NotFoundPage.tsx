import { useLocation } from "react-router-dom";
import Button from "@/components/Button";
import { CARD, MONO, cx } from "@/components/classes";

/** Any address the app does not know. */
export default function NotFoundPage() {
  const { pathname } = useLocation();
  return (
    <section className={cx(CARD, "max-w-[640px]")}>
      <h1 className="mx-0 mt-0 mb-2 text-[1.375rem] font-bold">Page not found</h1>
      <p className="mx-0 mt-0 mb-2">
        There is no page at <span className={cx(MONO, "[overflow-wrap:anywhere]")}>{pathname}</span>. It may have moved, or the link was typed wrong.
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" size="sm" to="/">
          Go to Home
        </Button>
        <Button
          size="sm"
          type="button"
          onClick={() => window.dispatchEvent(new Event("open-command-palette"))}
        >
          Search
        </Button>
      </div>
    </section>
  );
}
