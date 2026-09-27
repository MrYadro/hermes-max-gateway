import { useEffect, useState, type ReactNode } from "react";
import {
  Check,
  Megaphone,
  MessageCircle,
  Settings,
  User,
  Users,
} from "lucide-react";
import {
  Avatar,
  CellAction,
  CellHeader,
  Flex,
  CellList,
  CellSimple,
  EllipsisText,
  Container,
  Panel,
  Spinner,
  Typography,
} from "@maxhub/max-ui";
import { api, CurrentChat, currentChat, devFocus, StateResponse } from "./api";

const ICON_SIZE = 20;

const TYPE_META: Record<string, { Icon: typeof User; label: string }> = {
  dm: { Icon: User, label: "Личный чат" },
  group: { Icon: Users, label: "Группа" },
  channel: { Icon: Megaphone, label: "Канал" },
  unknown: { Icon: MessageCircle, label: "Чат" },
};

const GRADIENTS = ["red", "orange", "green", "blue", "purple"] as const;
type Gradient = (typeof GRADIENTS)[number];

/** Детерминированный градиент по имени: у каждого профиля свой стабильный цвет */
function gradientOf(name: string): Gradient {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return GRADIENTS[h % GRADIENTS.length];
}

function ProfileAvatar({ name, size = 40 }: { name: string; size?: number }) {
  // «default» закреплён официальный оранжевый градиент MAX UI — единство с иконкой бота
  const gradient = name === "default" ? "orange" : gradientOf(name);
  return (
    <Avatar.Container size={size} form="squircle">
      <Avatar.Text gradient={gradient}>{name.slice(0, 1).toUpperCase()}</Avatar.Text>
    </Avatar.Container>
  );
}

/** Аватар чата: градиент по chat_id, буква названия (нет названия — иконка типа) */
function ChatAvatar({ chatId, title, type, size = 36 }: {
  chatId: string; title?: string; type: string; size?: number;
}) {
  const meta = TYPE_META[type] ?? TYPE_META.unknown;
  const glyph = title?.trim() ? title.trim().slice(0, 1).toUpperCase() : <meta.Icon size={size * 0.5} strokeWidth={2} />;
  return (
    <Avatar.Container size={size} form="squircle">
      <Avatar.Text gradient={gradientOf(chatId)}>{glyph}</Avatar.Text>
    </Avatar.Container>
  );
}

type View = "chat" | "chats";

export default function App() {
  const here = currentChat();
  const [state, setState] = useState<StateResponse | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState<View>(here ? "chat" : "chats");
  const [focus, setFocus] = useState<string | null>(devFocus() ?? here?.id ?? null);
  const [busy, setBusy] = useState(false);

  const openChat = (chatId: string) => {
    setFocus(chatId);
    setView("chat");
  };

  const load = () => api.state().then(setState).catch((e) => setError(String(e.message ?? e)));

  useEffect(() => { load(); }, []);

  /** Нативная «Назад» (MAX Bridge): видна везде, кроме корневого экрана */
  useEffect(() => {
    const bb = window.WebApp?.BackButton;
    if (!bb) return;
    const atRoot = here ? view === "chat" && focus === here.id : view === "chats";
    if (atRoot) {
      bb.hide();
      return;
    }
    const cb = () => {
      if (view === "chats" && here) openChat(here.id);
      else setView("chats");
    };
    bb.onClick(cb);
    bb.show();
    return () => bb.offClick(cb);
  }, [view, focus, here]);

  const pick = async (chatId: string, profile: string) => {
    setBusy(true);
    try {
      const r = await api.set(chatId, profile);
      setState(r.state);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <Panel mode="secondary" centeredX centeredY>
        <Typography.Body>{error}</Typography.Body>
        <CellAction mode="secondary" onClick={() => { setError(""); load(); }}>
          Повторить
        </CellAction>
      </Panel>
    );
  }
  if (!state) {
    return (
      <Panel mode="secondary" centeredX centeredY>
        <Spinner />
      </Panel>
    );
  }

  const profileOf = (chatId: string) =>
    state.chats.find((c) => c.chat_id === chatId)?.profile ?? "default";
  const typeOf = (chatId: string) =>
    state.chats.find((c) => c.chat_id === chatId)?.chat_type ?? here?.type ?? "unknown";

  /** Экран текущего чата: чистый выбор профиля — аватар, имя, серое описание */
  const ProfilePicker = ({ chatId, header }: { chatId: string; header?: ReactNode }) => {
    const current = profileOf(chatId);
    return (
      <CellList mode="island" header={header}>
        {state.profiles.map((p) => {
          const isCurrent = p.name === current;
          return (
            <CellSimple
              key={p.name}
              height="compact"
              before={<ProfileAvatar name={p.name} size={36} />}
              title={<EllipsisText maxLines={1}>{p.name}</EllipsisText>}
              subtitle={
                p.description ? <EllipsisText maxLines={1}>{p.description}</EllipsisText> : undefined
              }
              after={
                <span style={{ width: 24, display: "inline-flex", justifyContent: "center" }}>
                  {isCurrent ? <Check size={20} strokeWidth={2} /> : null}
                </span>
              }
              disabled={busy}
              onClick={() => !isCurrent && pick(chatId, p.name)}
            />
          );
        })}
      </CellList>
    );
  };

  return (
    <Panel mode="secondary">
      <Container>
        <Flex direction="column" gap={16}>
          {view === "chat" && focus ? (
            <>
              <ProfilePicker
                chatId={focus}
                header={
                  here && focus !== here.id ? (
                    <CellHeader>
                      {state.chats.find((c) => c.chat_id === focus)?.title || focus}
                    </CellHeader>
                  ) : undefined
                }
              />
              <CellList mode="island">
                <CellSimple
                  showChevron
                  before={<Settings size={ICON_SIZE} strokeWidth={2} />}
                  title="Настройки"
                  subtitle="Все чаты и их ассистенты"
                  onClick={() => setView("chats")}
                />
              </CellList>
            </>
          ) : (
            <>
              {state.chats.length === 0 ? (
                <Typography.Body>Чатов пока нет — напишите боту.</Typography.Body>
              ) : (
                <CellList mode="island">
                  {state.chats.map((c) => {
                    const meta = TYPE_META[c.chat_type] ?? TYPE_META.unknown;
                    return (
                      <CellSimple
                        key={c.chat_id}
                        height="compact"
                        showChevron
                        before={<ChatAvatar chatId={c.chat_id} title={c.title} type={c.chat_type} />}
                        title={<EllipsisText maxLines={1}>{c.title || c.chat_id}</EllipsisText>}
                        subtitle={<EllipsisText maxLines={1}>{meta.label}</EllipsisText>}
                        after={<Typography.Body variant="small">{c.profile}</Typography.Body>}
                        onClick={() => openChat(c.chat_id)}
                      />
                    );
                  })}
                </CellList>
              )}
            </>
          )}
        </Flex>
      </Container>
    </Panel>
  );
}
