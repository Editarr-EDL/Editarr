import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { OperatorAccess } from "./operator-access";
import "../../styles/community-operator.css";
import "../../styles/operator/shell.css";
import "../../styles/operator/home.css";
import "../../styles/operator/catalog.css";
import "../../styles/operator/system.css";

createRoot(document.getElementById("root")!).render(<StrictMode><OperatorAccess /></StrictMode>);
