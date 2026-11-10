import { useEffect, useState } from "react";
import { Button } from "antd";
import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { hubApi, type HubUser } from "../../api/modules/hub";
import MarketplacePanel from "./MarketplacePanel";

export default function MarketplacePage() {
  const navigate = useNavigate();
  const [me, setMe] = useState<HubUser | null>(null);

  useEffect(() => {
    void hubApi.me().then(setMe).catch(() => setMe(null));
  }, []);

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg-layout)" }}>
      <div style={{ padding: "16px 24px 0" }}>
        <Button
          type="text"
          icon={<ArrowLeft size={16} />}
          onClick={() => navigate(-1)}
        >
          返回
        </Button>
      </div>
      {me ? <MarketplacePanel role={me.role} /> : null}
    </div>
  );
}
