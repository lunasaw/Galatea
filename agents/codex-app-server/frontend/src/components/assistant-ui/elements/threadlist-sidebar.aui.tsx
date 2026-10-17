// Source: assistant-ui/assistant-ui @ 2bdd534a3d8da45e6c7a9ee6553862b83713059d (MIT). See UPSTREAM.md.
import type * as React from "react";
import { BRAND_NAME, BrandIcon } from "@/brand";
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
  headerActions,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
  footer?: React.ReactNode;
  navigation?: React.ReactNode;
  headerActions?: React.ReactNode;
}) {
  return (
    <Sidebar className="workbench-sidebar" {...props}>
      <SidebarHeader className="aui-sidebar-header">
        <div className="aui-sidebar-header-content flex items-center justify-between">
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" asChild>
                <a href="/" aria-label={`${BRAND_NAME} 首页`}>
                  <div className="aui-sidebar-header-icon-wrapper flex aspect-square size-8 items-center justify-center">
                    <BrandIcon />
                  </div>
                  <div className="aui-sidebar-header-heading me-6 flex flex-col gap-0.5 leading-none">
                    <span className="aui-sidebar-header-title font-semibold">
                      {BRAND_NAME}
                    </span>
                  </div>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
          {headerActions}<SidebarTrigger aria-label="收起侧栏" />
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
