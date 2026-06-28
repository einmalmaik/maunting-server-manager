import { Copy } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { PostgresCredential } from "@/types";

interface Props {
  credentials: PostgresCredential[];
  onClose: () => void;
}

export function PostgresCredentialsDialog({ credentials, onClose }: Props) {
  const { t } = useTranslation();
  if (!credentials.length) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="msm-card w-full max-w-2xl p-6">
        <h2 className="font-headline text-headline-sm text-primary mb-2">
          {t("servers.postgres.credentialsTitle")}
        </h2>
        <p className="font-body-md text-sm text-on-surface-variant mb-4">
          {t("servers.postgres.credentialsHint")}
        </p>
        <div className="space-y-3">
          {credentials.map((cred) => {
            const dsn = `postgresql://${cred.username}:${cred.password}@${cred.host}:${cred.port}/${cred.database_name}`;
            return (
              <div key={`${cred.database_name}-${cred.username}`} className="rounded-lg border border-outline-variant bg-surface-container p-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 font-mono text-sm text-on-surface">
                  <div>{t("databases.database")}: {cred.database_name}</div>
                  <div>{t("databases.user")}: {cred.username}</div>
                  <div>{t("databases.host")}: {cred.host}</div>
                  <div>{t("databases.port")}: {cred.port}</div>
                  <div className="md:col-span-2">{t("databases.password")}: {cred.password}</div>
                  <div className="md:col-span-2 break-all">
                    {t("databases.connectionUrl")}: {dsn}
                  </div>
                </div>
                <button
                  type="button"
                  className="msm-btn-secondary mt-3 inline-flex items-center gap-2 px-3 py-2"
                  onClick={() => navigator.clipboard.writeText(dsn)}
                >
                  <Copy className="w-4 h-4" />
                  {t("servers.postgres.copyDsn")}
                </button>
              </div>
            );
          })}
        </div>
        <div className="flex justify-end mt-5">
          <button type="button" className="msm-btn-primary px-4 py-2" onClick={onClose}>
            {t("common.close")}
          </button>
        </div>
      </div>
    </div>
  );
}
