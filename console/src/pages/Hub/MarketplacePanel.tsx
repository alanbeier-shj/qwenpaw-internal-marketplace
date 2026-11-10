import { useEffect, useMemo, useState, type Key } from "react";
import {
  App,
  Button,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Upload,
} from "antd";
import type { UploadFile } from "antd";
import {
  Check,
  Download,
  Inbox,
  PackagePlus,
  Send,
  X,
} from "lucide-react";
import {
  hubMarketplaceApi,
  type MarketplaceItem,
  type MarketplaceStatus,
} from "../../api/modules/hubMarketplace";
import type { HubUser } from "../../api/modules/hub";

const STATUS_COLORS: Record<MarketplaceStatus, string> = {
  pending: "processing",
  published: "success",
  rejected: "error",
};

const STATUS_LABELS: Record<MarketplaceStatus, string> = {
  pending: "待审核",
  published: "已发布",
  rejected: "已驳回",
};

export default function MarketplacePanel({ role }: { role: HubUser["role"] }) {
  const { message } = App.useApp();
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<"all" | MarketplaceStatus>(
    role === "admin" ? "pending" : "published",
  );
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Key[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [review, setReview] = useState<{
    ids: string[];
    action: "approve" | "reject";
  } | null>(null);
  const [reviewNote, setReviewNote] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const result = await hubMarketplaceApi.list({
        status: status === "all" ? undefined : status,
        query,
      });
      setItems(result.items);
    } catch (error) {
      message.error(
        error instanceof Error ? error.message : "加载技能市场失败",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // Status and query are intentionally reload triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, query]);

  const uploadFiles = async (fileList: UploadFile[]) => {
    const files = fileList
      .map((file) => file.originFileObj)
      .filter((file): file is File => file instanceof File);
    if (files.length === 0) return;
    setLoading(true);
    try {
      const result = await hubMarketplaceApi.upload(files);
      message.success(`已提交 ${result.items.length} 个技能`);
      setStatus("pending");
      await load();
    } catch (error) {
      message.error(
        error instanceof Error ? error.message : "技能上传失败",
      );
    } finally {
      setLoading(false);
    }
  };

  const submitReview = async () => {
    if (!review) return;
    setBusyId(review.ids.join(","));
    try {
      await hubMarketplaceApi.review(review.ids, review.action, reviewNote);
      message.success(
        review.action === "approve" ? "已发布" : "已驳回",
      );
      setReview(null);
      setReviewNote("");
      setSelected([]);
      await load();
    } catch (error) {
      message.error(
        error instanceof Error ? error.message : "审核失败",
      );
    } finally {
      setBusyId(null);
    }
  };

  const runItemAction = async (
    item: MarketplaceItem,
    action: "install" | "broadcast",
  ) => {
    setBusyId(item.id);
    try {
      if (action === "install") {
        await hubMarketplaceApi.install(item.id);
        message.success("已安装到当前技能池");
      } else {
        const result = await hubMarketplaceApi.broadcast(item.id);
        message.success(
          `已分发到 ${result.installed.length} 个用户，失败 ${result.failed.length} 个`,
        );
      }
    } catch (error) {
      message.error(
        error instanceof Error ? error.message : "操作失败",
      );
    } finally {
      setBusyId(null);
    }
  };

  const columns = useMemo(
    () => [
      {
        title: "技能",
        dataIndex: "name",
        render: (_: unknown, item: MarketplaceItem) => (
          <div>
            <div style={{ fontWeight: 600 }}>
              {item.emoji ? `${item.emoji} ` : ""}
              {item.name}
            </div>
            <div style={{ color: "rgba(0,0,0,0.45)", fontSize: 12 }}>
              {item.description}
            </div>
          </div>
        ),
      },
      {
        title: "版本",
        dataIndex: "version",
        width: 90,
        render: (value: string) => value || "—",
      },
      {
        title: "提交人",
        dataIndex: "submitter_username",
        width: 140,
      },
      {
        title: "状态",
        dataIndex: "status",
        width: 110,
        render: (value: MarketplaceStatus) => (
          <Tag color={STATUS_COLORS[value]}>{STATUS_LABELS[value]}</Tag>
        ),
      },
      {
        title: "更新时间",
        dataIndex: "updated_at",
        width: 170,
        render: (value: string) => new Date(value).toLocaleString(),
      },
      {
        title: "操作",
        width: role === "admin" ? 280 : 190,
        render: (_: unknown, item: MarketplaceItem) => (
          <Space size={4}>
            {role === "admin" && item.status === "pending" && (
              <>
                <Button
                  size="small"
                  type="primary"
                  icon={<Check size={14} />}
                  onClick={() => setReview({ ids: [item.id], action: "approve" })}
                >
                  通过
                </Button>
                <Button
                  size="small"
                  danger
                  icon={<X size={14} />}
                  onClick={() => setReview({ ids: [item.id], action: "reject" })}
                >
                  驳回
                </Button>
              </>
            )}
            {(item.status === "published" || role === "admin") && (
              <>
                <Button
                  size="small"
                  icon={<Download size={14} />}
                  loading={busyId === item.id}
                  onClick={() => void hubMarketplaceApi.download(item)}
                >
                  下载
                </Button>
                {item.status === "published" && (
                  <Button
                    size="small"
                    icon={<Inbox size={14} />}
                    loading={busyId === item.id}
                    onClick={() => void runItemAction(item, "install")}
                  >
                    安装
                  </Button>
                )}
                {role === "admin" && item.status === "published" && (
                  <Button
                    size="small"
                    icon={<Send size={14} />}
                    loading={busyId === item.id}
                    onClick={() => void runItemAction(item, "broadcast")}
                  >
                    分发
                  </Button>
                )}
              </>
            )}
          </Space>
        ),
      },
    ],
    [busyId, role],
  );

  const batchReview = (action: "approve" | "reject") => {
    if (selected.length === 0) return;
    setReview({ ids: selected.map(String), action });
  };

  return (
    <div style={{ padding: 24 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <div>
          <h2 style={{ margin: 0 }}>内网技能市场</h2>
          <div style={{ color: "rgba(0,0,0,0.45)" }}>
            上传的技能经管理员审核后发布，用户可安装到自己的技能池。
          </div>
        </div>
        <Upload
          multiple
          accept=".zip"
          showUploadList={false}
          beforeUpload={() => false}
          onChange={({ fileList }) => {
            if (fileList.some((file) => file.originFileObj)) {
              void uploadFiles(fileList);
            }
          }}
        >
          <Button type="primary" icon={<PackagePlus size={16} />}>
            批量上传
          </Button>
        </Upload>
      </div>

      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          allowClear
          value={query}
          placeholder="搜索技能"
          style={{ width: 240 }}
          onChange={(event) => setQuery(event.target.value)}
        />
        {role === "admin" && (
          <Select
            value={status}
            style={{ width: 140 }}
            onChange={(value) =>
              setStatus(value as "all" | MarketplaceStatus)
            }
            options={[
              { value: "all", label: "全部" },
              { value: "pending", label: "待审核" },
              { value: "published", label: "已发布" },
              { value: "rejected", label: "已驳回" },
            ]}
          />
        )}
        {role === "admin" && selected.length > 0 && (
          <Space>
            <Button
              type="primary"
              icon={<Check size={14} />}
              onClick={() => batchReview("approve")}
            >
              批量通过
            </Button>
            <Button
              danger
              icon={<X size={14} />}
              onClick={() => batchReview("reject")}
            >
              批量驳回
            </Button>
          </Space>
        )}
      </Space>

      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        rowSelection={
          role === "admin"
            ? {
                selectedRowKeys: selected,
                onChange: (keys: Key[]) => setSelected(keys),
              }
            : undefined
        }
      />

      <Modal
        open={review !== null}
        title={review?.action === "approve" ? "发布技能" : "驳回技能"}
        okText={review?.action === "approve" ? "确认发布" : "确认驳回"}
        okButtonProps={{ danger: review?.action === "reject" }}
        confirmLoading={busyId !== null}
        onCancel={() => {
          setReview(null);
          setReviewNote("");
        }}
        onOk={() => void submitReview()}
      >
        <Input.TextArea
          rows={3}
          value={reviewNote}
          placeholder="审核备注（可选）"
          onChange={(event) => setReviewNote(event.target.value)}
        />
      </Modal>
    </div>
  );
}
