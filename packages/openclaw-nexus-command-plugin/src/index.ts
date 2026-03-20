const PLUGIN_ID = "nexus-arc-command";

type PluginConfig = {
  bridgeUrl?: string;
  authToken?: string;
  timeoutMs?: number;
  sourcePlatform?: string;
};

type CommandHandlerContext = {
  senderId?: string;
  senderName?: string;
  channel?: string;
  channelId?: string;
  isAuthorizedSender?: boolean;
  args?: string;
  commandBody?: string;
  config?: Record<string, unknown>;
};

type CommandResponse = {
  text: string;
};

type RegisterCommandApi = {
  registerCommand: (command: {
    name: string;
    description: string;
    acceptsArgs?: boolean;
    requireAuth?: boolean;
    handler: (ctx: CommandHandlerContext) => Promise<CommandResponse> | CommandResponse;
  }) => void;
};

type NexusCommandResult = {
  status: string;
  message: string;
  workflow_id?: string | null;
  issue_number?: string | null;
  project_key?: string | null;
  suggested_next_commands?: string[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function resolvePluginConfig(ctx: CommandHandlerContext): Required<PluginConfig> {
  const root = isRecord(ctx.config) ? ctx.config : {};
  const plugins = isRecord(root.plugins) ? root.plugins : {};
  const entries = isRecord(plugins.entries) ? plugins.entries : {};
  const pluginEntry = isRecord(entries[PLUGIN_ID]) ? entries[PLUGIN_ID] : {};
  const pluginConfig = isRecord(pluginEntry.config) ? pluginEntry.config : {};

  return {
    bridgeUrl:
      stringValue(pluginConfig.bridgeUrl) ||
      process.env.NEXUS_COMMAND_BRIDGE_URL ||
      "http://127.0.0.1:8091",
    authToken:
      stringValue(pluginConfig.authToken) ||
      process.env.NEXUS_COMMAND_BRIDGE_TOKEN ||
      "",
    timeoutMs: numberValue(pluginConfig.timeoutMs) ?? 15000,
    sourcePlatform:
      stringValue(pluginConfig.sourcePlatform) ||
      process.env.NEXUS_COMMAND_BRIDGE_SOURCE_PLATFORM ||
      "openclaw"
  };
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function tokenizeArgs(rawArgs: string | undefined): string[] {
  return String(rawArgs ?? "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
}

function parseNexusInvocation(rawArgs: string | undefined): {
  command: string;
  args: string[];
  freeform: string;
} {
  const freeform = String(rawArgs ?? "").trim();
  const tokens = tokenizeArgs(rawArgs);
  if (tokens.length === 0) {
    return { command: "", args: [], freeform: "" };
  }
  const [command, ...args] = tokens;
  return {
    command: command.toLowerCase(),
    args,
    freeform
  };
}

function requesterFromContext(
  ctx: CommandHandlerContext,
  config: Required<PluginConfig>
): Record<string, unknown> {
  return {
    source_platform: config.sourcePlatform,
    sender_id: String(ctx.senderId ?? ""),
    sender_name: String(ctx.senderName ?? ""),
    channel_id: String(ctx.channelId ?? ""),
    channel_name: String(ctx.channel ?? ""),
    is_authorized_sender:
      typeof ctx.isAuthorizedSender === "boolean" ? ctx.isAuthorizedSender : undefined,
    metadata: {
      command_body: String(ctx.commandBody ?? "")
    }
  };
}

function isBoundedBridgeCommand(command: string): boolean {
  return new Set([
    "status",
    "active",
    "logs",
    "wfstate",
    "plan",
    "prepare",
    "implement",
    "respond",
    "pause",
    "resume",
    "stop",
    "continue",
    "agents",
    "audit",
    "stats"
  ]).has(command);
}

async function callBridge(
  path: string,
  payload: Record<string, unknown>,
  config: Required<PluginConfig>
): Promise<NexusCommandResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);

  try {
    const headers: Record<string, string> = {
      "content-type": "application/json"
    };
    if (config.authToken) {
      headers.authorization = `Bearer ${config.authToken}`;
    }

    const response = await fetch(`${config.bridgeUrl}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: controller.signal
    });

    const responseText = await response.text();
    const parsed = responseText ? safeJsonParse(responseText) : {};

    if (!response.ok) {
      const errorMessage =
        isRecord(parsed) && typeof parsed.error === "string"
          ? parsed.error
          : `HTTP ${response.status}`;
      throw new Error(errorMessage);
    }

    if (!isRecord(parsed) || typeof parsed.message !== "string") {
      throw new Error("Bridge returned an invalid response");
    }

    return parsed as NexusCommandResult;
  } finally {
    clearTimeout(timer);
  }
}

function safeJsonParse(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

function renderResult(result: NexusCommandResult): string {
  const lines: string[] = [result.message];
  if (result.workflow_id) {
    lines.push(`Workflow: ${result.workflow_id}`);
  }
  if (Array.isArray(result.suggested_next_commands) && result.suggested_next_commands.length > 0) {
    lines.push(`Next: ${result.suggested_next_commands.join(" | ")}`);
  }
  return lines.filter(Boolean).join("\n");
}

async function handleNexusCommand(ctx: CommandHandlerContext): Promise<CommandResponse> {
  const config = resolvePluginConfig(ctx);
  const parsed = parseNexusInvocation(ctx.args);

  if (!parsed.command && !parsed.freeform) {
    return {
      text:
        "Usage: /nexus <command>. Examples: /nexus status demo, /nexus plan demo#42, /nexus implement demo#42."
    };
  }

  const requester = requesterFromContext(ctx, config);
  const result =
    parsed.command && isBoundedBridgeCommand(parsed.command)
      ? await callBridge(
          "/api/v1/commands/execute",
          {
            command: parsed.command,
            args: parsed.args,
            raw_text: parsed.freeform,
            requester
          },
          config
        )
      : await callBridge(
          "/api/v1/commands/route",
          {
            raw_text: parsed.freeform || ctx.commandBody || "",
            requester
          },
          config
        );

  return { text: renderResult(result) };
}

const plugin = {
  id: PLUGIN_ID,
  name: "Nexus ARC Command Bridge",
  configSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      bridgeUrl: { type: "string" },
      authToken: { type: "string" },
      timeoutMs: { type: "integer" },
      sourcePlatform: { type: "string" }
    }
  },
  register(api: RegisterCommandApi): void {
    api.registerCommand({
      name: "nexus",
      description: "Forward commands to the Nexus ARC command bridge",
      acceptsArgs: true,
      requireAuth: true,
      handler: handleNexusCommand
    });
  }
};

export default plugin;
