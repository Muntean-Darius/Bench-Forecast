/**
 * Mock data seeded from bench_forecast/data/mock_data.json.
 * Swap out for real API calls once the FastAPI backend is running.
 */

import type { Employee, Project, ProjectRole, AllocationProposal } from '../types'

export const MOCK_EMPLOYEES: Employee[] = [
  {
    id: 'a1b2c3d4-0001-0001-0001-000000000001',
    full_name: 'Alexandru Popescu',
    primary_role: 'Backend Engineer',
    seniority: 'Senior',
    hourly_cost_rate: 65,
    bench_start_date: '2026-09-20',
    cv_document_uri: 'cvs/alexandru_popescu.pdf',
    experience_years: 6.5,
    skills: 'Java, Spring Boot, Apache Kafka, PostgreSQL, Docker, AWS, Microservices',
  },
  {
    id: 'a1b2c3d4-0002-0002-0002-000000000002',
    full_name: 'Elena Radu',
    primary_role: 'AI/ML Engineer',
    seniority: 'Mid',
    hourly_cost_rate: 55,
    bench_start_date: '2026-10-01',
    cv_document_uri: 'cvs/elena_radu.pdf',
    experience_years: 4,
    skills: 'FastAPI, Python, LangChain, ChromaDB, RAG, Pandas, Machine Learning',
  },
  {
    id: 'a1b2c3d4-0003-0003-0003-000000000003',
    full_name: 'Mihai Ionescu',
    primary_role: 'Frontend Engineer',
    seniority: 'Senior',
    hourly_cost_rate: 60,
    bench_start_date: '2026-09-25',
    cv_document_uri: 'cvs/mihai_ionescu.pdf',
    experience_years: 5,
    skills: 'React, TypeScript, Next.js, Redux Toolkit, Tailwind CSS, GraphQL',
  },
  {
    id: 'a1b2c3d4-0004-0004-0004-000000000004',
    full_name: 'Cristian Vasile',
    primary_role: 'DevOps Engineer',
    seniority: 'Mid',
    hourly_cost_rate: 50,
    bench_start_date: '2026-09-15',
    cv_document_uri: 'cvs/cristian_vasile.pdf',
    experience_years: 3.5,
    skills: 'Terraform, Kubernetes, AWS EKS, GitHub Actions, GitLab CI, DevSecOps',
  },
  {
    id: 'a1b2c3d4-0005-0005-0005-000000000005',
    full_name: 'Andreea Dumitrescu',
    primary_role: 'Data Engineer',
    seniority: 'Senior',
    hourly_cost_rate: 70,
    bench_start_date: '2026-10-10',
    cv_document_uri: 'cvs/andreea_dumitrescu.pdf',
    experience_years: 5.5,
    skills: 'Apache Kafka, PySpark, Airflow, Snowflake, ETL, Financial Analytics',
  },
  {
    id: 'a1b2c3d4-0006-0006-0006-000000000006',
    full_name: 'Vlad Georgescu',
    primary_role: '.NET Engineer',
    seniority: 'Senior',
    hourly_cost_rate: 68,
    bench_start_date: '2026-09-28',
    cv_document_uri: 'cvs/vlad_georgescu.pdf',
    experience_years: 7,
    skills: 'ASP.NET Core, Azure, DDD, Azure Service Bus, AKS, SQL Server',
  },
  {
    id: 'a1b2c3d4-0007-0007-0007-000000000007',
    full_name: 'Ioana Moraru',
    primary_role: 'Mobile Engineer',
    seniority: 'Mid',
    hourly_cost_rate: 52,
    bench_start_date: '2026-10-05',
    cv_document_uri: 'cvs/ioana_moraru.pdf',
    experience_years: 4.5,
    skills: 'Flutter, Dart, iOS, Android, Biometric Auth, SQLite, FinTech',
  },
  {
    id: 'a1b2c3d4-0008-0008-0008-000000000008',
    full_name: 'Dan Stanescu',
    primary_role: 'QA Automation Engineer',
    seniority: 'Senior',
    hourly_cost_rate: 55,
    bench_start_date: '2026-09-18',
    cv_document_uri: 'cvs/dan_stanescu.pdf',
    experience_years: 6,
    skills: 'Playwright, PyTest, JMeter, REST API Testing, CI/CD, Python',
  },
  {
    id: 'a1b2c3d4-0010-0010-0010-000000000010',
    full_name: 'Bogdan Enache',
    primary_role: 'Backend Engineer',
    seniority: 'Principal',
    hourly_cost_rate: 95,
    bench_start_date: '2026-09-30',
    cv_document_uri: 'cvs/bogdan_enache.pdf',
    experience_years: 8,
    skills: 'Spring Boot, RabbitMQ, SAP Integration, Oracle DB, Software Architecture',
  },
  {
    id: 'a1b2c3d4-0012-0012-0012-000000000012',
    full_name: 'Radu Cernat',
    primary_role: 'Backend Engineer',
    seniority: 'Senior',
    hourly_cost_rate: 72,
    bench_start_date: '2026-09-12',
    cv_document_uri: 'cvs/radu_cernat.pdf',
    experience_years: 5,
    skills: 'Go, gRPC, Kubernetes, Prometheus, Grafana, Microservices',
  },
]

export const MOCK_PROJECTS: Project[] = [
  { id: 'p1', name: 'Core Banking Migration',             status: 'Active',    probability: 95, target_margin: 32, total_budget: 1200000 },
  { id: 'p2', name: 'AI Healthcare Assistant',            status: 'Active',    probability: 90, target_margin: 28, total_budget: 450000  },
  { id: 'p3', name: 'FinTech Real-Time Analytics',        status: 'Pipeline',  probability: 80, target_margin: 35, total_budget: 780000  },
  { id: 'p4', name: 'Insurance Claims Cloud Portal',      status: 'Active',    probability: 88, target_margin: 30, total_budget: 960000  },
  { id: 'p5', name: 'Retail E-Commerce Replatform',       status: 'Active',    probability: 92, target_margin: 25, total_budget: 550000  },
  { id: 'p6', name: 'Telco Cloud Security Modernization', status: 'Pipeline',  probability: 78, target_margin: 38, total_budget: 670000  },
  { id: 'p7', name: 'Mobile Banking Suite Upgrade',       status: 'Active',    probability: 85, target_margin: 27, total_budget: 390000  },
  { id: 'p8', name: 'Enterprise QA Automation Framework', status: 'Pipeline',  probability: 82, target_margin: 40, total_budget: 280000  },
]

export const MOCK_ROLES: ProjectRole[] = [
  {
    id: 'r1', project_id: 'p1', project_name: 'Core Banking Migration', project_status: 'Active',
    title: 'Senior Java Microservices Engineer',
    target_bill_rate: 110, status: 'Open', headcount: 1, start_date: '2026-10-01',
    required_skills: 'Spring Boot 3, Apache Kafka, PostgreSQL, Docker, AWS',
  },
  {
    id: 'r2', project_id: 'p2', project_name: 'AI Healthcare Assistant', project_status: 'Active',
    title: 'Python AI and Backend Developer',
    target_bill_rate: 95, status: 'Open', headcount: 1, start_date: '2026-10-15',
    required_skills: 'FastAPI, LangChain, ChromaDB, RAG, Python',
  },
  {
    id: 'r3', project_id: 'p3', project_name: 'FinTech Real-Time Analytics', project_status: 'Pipeline',
    title: 'Data Platform and Streaming Engineer',
    target_bill_rate: 105, status: 'Open', headcount: 1, start_date: '2026-11-01',
    required_skills: 'Apache Kafka, PySpark, Airflow, Snowflake',
  },
  {
    id: 'r4', project_id: 'p4', project_name: 'Insurance Claims Cloud Portal', project_status: 'Active',
    title: 'Senior .NET Azure Cloud Specialist',
    target_bill_rate: 115, status: 'Open', headcount: 1, start_date: '2026-10-01',
    required_skills: 'ASP.NET Core, Azure Service Bus, SQL Server, DDD',
  },
  {
    id: 'r5', project_id: 'p5', project_name: 'Retail E-Commerce Replatform', project_status: 'Active',
    title: 'Senior React & TypeScript Frontend Lead',
    target_bill_rate: 100, status: 'Open', headcount: 1, start_date: '2026-10-10',
    required_skills: 'React 18, Next.js, GraphQL, TypeScript, Tailwind CSS',
  },
  {
    id: 'r6', project_id: 'p6', project_name: 'Telco Cloud Security Modernization', project_status: 'Pipeline',
    title: 'DevOps & Cloud Infrastructure Architect',
    target_bill_rate: 120, status: 'Open', headcount: 1, start_date: '2026-11-15',
    required_skills: 'Terraform, Kubernetes EKS, AWS, DevSecOps, GitHub Actions',
  },
  {
    id: 'r7', project_id: 'p7', project_name: 'Mobile Banking Suite Upgrade', project_status: 'Active',
    title: 'Mobile Engineer — Cross-Platform Flutter',
    target_bill_rate: 90, status: 'Open', headcount: 1, start_date: '2026-10-05',
    required_skills: 'Flutter, Dart, Biometric Auth, iOS, Android, Security',
  },
  {
    id: 'r8', project_id: 'p8', project_name: 'Enterprise QA Automation Framework', project_status: 'Pipeline',
    title: 'Lead QA Automation Engineer',
    target_bill_rate: 85, status: 'Open', headcount: 1, start_date: '2026-11-01',
    required_skills: 'Playwright, PyTest, JMeter, REST API Testing, Python',
  },
]

// ─── Compute margin helper ────────────────────────────────────────────────────

function computeMargin(billRate: number, costRate: number) {
  return Math.round(((billRate - costRate) / billRate) * 100 * 100) / 100
}

// ─── AI Proposals (mock HITL queue) ──────────────────────────────────────────

export const MOCK_PROPOSALS: AllocationProposal[] = [
  {
    id: 'prop-001',
    employee: MOCK_EMPLOYEES[0],    // Alexandru Popescu — Java/Spring
    role: MOCK_ROLES[0],            // Senior Java Microservices
    project: MOCK_PROJECTS[0],
    category: 'direct',
    match_score: 0.94,
    projected_margin: computeMargin(110, 65),
    reasoning: 'Alexandru has 6.5 years of Spring Boot and Apache Kafka experience — an exact match for the Core Banking Migration microservices role. His AWS Docker skills directly cover the infrastructure requirements.',
    missing_skills: [],
    rag_passages: [
      'Led migration of two enterprise monolithic applications to microservices using Spring Boot 3 and Apache Kafka.',
      'Strong skills in PostgreSQL query tuning and Docker containerization on AWS.',
    ],
    created_at: '2026-09-19T08:12:00Z',
  },
  {
    id: 'prop-002',
    employee: MOCK_EMPLOYEES[1],    // Elena Radu — AI/ML
    role: MOCK_ROLES[1],            // Python AI Backend
    project: MOCK_PROJECTS[1],
    category: 'direct',
    match_score: 0.91,
    projected_margin: computeMargin(95, 55),
    reasoning: 'Elena has hands-on LangChain and ChromaDB experience building a RAG document Q&A system — the exact tech stack required for the AI Healthcare Assistant backend.',
    missing_skills: [],
    rag_passages: [
      'Expert in building high-performance RESTful services with FastAPI and integrating predictive models.',
      'Has built a RAG-based document Q&A system for a retail analytics product.',
    ],
    created_at: '2026-09-19T08:15:00Z',
  },
  {
    id: 'prop-003',
    employee: MOCK_EMPLOYEES[4],    // Andreea Dumitrescu — Data
    role: MOCK_ROLES[2],            // Data Platform & Streaming
    project: MOCK_PROJECTS[2],
    category: 'direct',
    match_score: 0.88,
    projected_margin: computeMargin(105, 70),
    reasoning: 'Andreea has direct Kafka, PySpark, and Airflow experience building ETL pipelines for financial analytics — a strong semantic and technical match.',
    missing_skills: [],
    rag_passages: [
      'Designed real-time ingestion flows with Apache Kafka and PySpark, orchestrated by Apache Airflow.',
      'Expert in supply chain data modelling and financial transaction anomaly detection.',
    ],
    created_at: '2026-09-19T08:18:00Z',
  },
  {
    id: 'prop-004',
    employee: MOCK_EMPLOYEES[2],    // Mihai Ionescu — React
    role: MOCK_ROLES[4],            // React TS Frontend Lead
    project: MOCK_PROJECTS[4],
    category: 'direct',
    match_score: 0.89,
    projected_margin: computeMargin(100, 60),
    reasoning: 'Mihai has led Next.js and React dashboard projects with Tailwind CSS and GraphQL integration. Strong match for the Retail E-Commerce Replatform frontend lead role.',
    missing_skills: [],
    rag_passages: [
      'Led development of complex real-time analytical dashboards with Next.js, Redux Toolkit, and Tailwind CSS.',
      'Frequently integrates GraphQL and RESTful APIs with a focus on render performance.',
    ],
    created_at: '2026-09-19T08:20:00Z',
  },
  {
    id: 'prop-005',
    employee: MOCK_EMPLOYEES[3],    // Cristian Vasile — DevOps
    role: MOCK_ROLES[5],            // DevOps Architect Telco
    project: MOCK_PROJECTS[5],
    category: 'training',
    match_score: 0.72,
    projected_margin: computeMargin(120, 50),
    reasoning: 'Cristian covers Terraform and EKS well but lacks the security certification and DevSecOps depth required for the Telco Cloud Security project. A targeted DevSecOps certification course (est. 4 weeks) would close the gap.',
    missing_skills: ['DevSecOps Certification', 'Security Compliance Auditing'],
    training_recommendation: '4-week DevSecOps & Cloud Security Certification (AWS Security Specialty)',
    rag_passages: [
      'Designed and maintained robust CI/CD pipelines with security compliance in AWS environments.',
      'Managing Kubernetes clusters (EKS) and automating infrastructure with Terraform.',
    ],
    created_at: '2026-09-19T08:22:00Z',
  },
  {
    id: 'prop-006',
    employee: MOCK_EMPLOYEES[7],    // Dan Stanescu — QA
    role: MOCK_ROLES[7],            // Lead QA Automation
    project: MOCK_PROJECTS[7],
    category: 'direct',
    match_score: 0.96,
    projected_margin: computeMargin(85, 55),
    reasoning: 'Dan has built Playwright+PyTest frameworks from scratch and has JMeter load testing experience — a near-perfect match for the Enterprise QA Automation Framework role.',
    missing_skills: [],
    rag_passages: [
      'Built testing frameworks from scratch in Python with Playwright and PyTest.',
      'Extensive experience in REST API testing and load testing with Apache JMeter.',
    ],
    created_at: '2026-09-19T08:25:00Z',
  },
  {
    id: 'prop-007',
    employee: MOCK_EMPLOYEES[5],    // Vlad Georgescu — .NET
    role: MOCK_ROLES[3],            // Senior .NET Azure
    project: MOCK_PROJECTS[3],
    category: 'direct',
    match_score: 0.93,
    projected_margin: computeMargin(115, 68),
    reasoning: 'Vlad has 7 years of ASP.NET Core, Azure Service Bus, and AKS experience. His banking and insurance domain background is a precise cultural and technical match for the Insurance Claims portal.',
    missing_skills: [],
    rag_passages: [
      'Expert in ASP.NET Core Web API, DDD, and Microsoft SQL Server.',
      'Implemented asynchronous services with Azure Service Bus and Azure Kubernetes Service.',
    ],
    created_at: '2026-09-19T08:28:00Z',
  },
  {
    id: 'prop-008',
    role: MOCK_ROLES[1],
    project: MOCK_PROJECTS[1],
    category: 'hiring',
    match_score: 0,
    projected_margin: 0,
    // Hiring recommendation — no employee match found
    employee: {
      id: '',
      full_name: 'No internal match',
      primary_role: 'AI/ML Engineer',
      seniority: 'Senior',
      hourly_cost_rate: 0,
      bench_start_date: '',
    },
    reasoning: 'No bench employee has sufficient LLM fine-tuning and medical NLP experience for the secondary AI Healthcare role headcount. External hiring recommended at €90-100/hr market rate.',
    missing_skills: ['LLM Fine-Tuning', 'Medical NLP', 'HuggingFace'],
    rag_passages: [],
    created_at: '2026-09-19T08:30:00Z',
  },
]

// ─── Bench utilization chart data (next 3 months) ────────────────────────────

export const BENCH_TREND_DATA = [
  { month: 'Oct W1', allocated: 3,  bench: 12 },
  { month: 'Oct W2', allocated: 6,  bench: 9  },
  { month: 'Oct W3', allocated: 8,  bench: 7  },
  { month: 'Nov W1', allocated: 9,  bench: 5  },
  { month: 'Nov W2', allocated: 10, bench: 4  },
  { month: 'Nov W3', allocated: 11, bench: 3  },
  { month: 'Dec W1', allocated: 11, bench: 2  },
  { month: 'Dec W2', allocated: 11, bench: 2  },
  { month: 'Dec W3', allocated: 12, bench: 1  },
]

// ─── Financial health chart data ──────────────────────────────────────────────

export const FINANCIAL_HEALTH_DATA = [
  { month: 'Oct', cost: 118400, revenue: 176000 },
  { month: 'Nov', cost: 124000, revenue: 196000 },
  { month: 'Dec', cost: 129200, revenue: 218000 },
]
