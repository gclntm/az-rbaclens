import { describe, expect, it } from "vitest";

import { entraRemoveCommand } from "./AssignmentDrawer";

describe("removing an Entra role", () => {
  it("finds the assignment through Microsoft Graph, then deletes it", () => {
    const cmd = entraRemoveCommand("ada-id", "/providers/microsoft.aadiam/roleDefinitions/62e90394-69f5-4237-9190-012177145e10");
    expect(cmd).toBe(
      'az rest --method DELETE --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments/' +
        '$(az rest --method GET --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments' +
        "?\\$filter=principalId eq 'ada-id' and roleDefinitionId eq '62e90394-69f5-4237-9190-012177145e10'\"" +
        ' --query "value[0].id" -o tsv)"',
    );
  });
});
