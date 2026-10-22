import { useBranding } from "../branding";
import { NewRequest } from "./NewRequest";

/** The portal's front page: the welcome text from Settings > Branding, then a new request. */
export function Home() {
  const brand = useBranding();
  return (
    <div className="stack" style={{ maxWidth: 720 }}>
      {brand.portal_welcome && (
        <div className="card" aria-label="Welcome" role="note">
          <p className="body-text">{brand.portal_welcome}</p>
        </div>
      )}
      <NewRequest />
    </div>
  );
}
