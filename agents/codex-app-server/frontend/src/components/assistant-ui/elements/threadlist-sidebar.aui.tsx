// Source: assistant-ui/assistant-ui @ 2bdd534a3d8da45e6c7a9ee6553862b83713059d (MIT). See UPSTREAM.md.
import type * as React from "react";
import { SquareTerminal } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { ThreadList } from "@/components/assistant-ui/elements/thread-list.aui";

export function ThreadListSidebar({
  footer,
  navigation,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
  footer?: React.ReactNode;
  navigation?: React.ReactNode;
}) {
  return (
    <Sidebar className="workbench-sidebar" {...props}>
      <SidebarHeader className="aui-sidebar-header">
        <div className="aui-sidebar-header-content flex items-center justify-between">
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" asChild>
                <a href="/">
                  <div className="aui-sidebar-header-icon-wrapper flex aspect-square size-8 items-center justify-center">
                    <SquareTerminal className="aui-sidebar-header-icon size-6" />
                  </div>
                  <div className="aui-sidebar-header-heading me-6 flex flex-col gap-0.5 leading-none">
                    <span className="aui-sidebar-header-title font-semibold">
                      Codex
                    </span>
                  </div>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
          <SidebarTrigger aria-label="收起侧栏" />
        </div>
      </SidebarHeader>
      <SidebarContent className="aui-sidebar-content px-2">
        <ThreadList navigation={navigation} />
      </SidebarContent>
      {props.collapsible !== "none" && <SidebarRail />}
      <SidebarFooter className="aui-sidebar-footer p-3">{footer}</SidebarFooter>
    </Sidebar>
  );
}
