import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { SpinnerIcon, AlertIcon, UserIcon } from '../components/icons/Icons'

interface Manager {
  id: string
  username: string
  email: string
  full_name: string
  role: string
  employee_id: string | null
  department_id: string | null
  is_active: boolean
  direct_reports_count: number
}

interface FormState {
  full_name: string
  email: string
  username: string
  password: string
  department_id: string
  employee_id: string
}

const EMPTY_FORM: FormState = {
  full_name: '',
  email: '',
  username: '',
  password: '',
  department_id: '',
  employee_id: '',
}

function generatePassword() {
  return 'Mgr-' + Math.random().toString(36).slice(2, 10)
}

export default function AdminManagers() {
  const [managers, setManagers] = useState<Manager[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  function loadManagers() {
    setLoading(true)
    api.getManagers()
      .then(setManagers)
      .catch((err) => setError(err?.message || 'Failed to load managers'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadManagers() }, [])

  function openModal() {
    setForm({ ...EMPTY_FORM, password: generatePassword() })
    setFormError(null)
    setSuccessMsg(null)
    setShowModal(true)
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    setForm((f) => ({ ...f, [e.target.name]: e.target.value }))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setFormError(null)
    setSubmitting(true)
    try {
      const res = await api.createManager({
        full_name: form.full_name,
        email: form.email,
        username: form.username,
        password: form.password,
        department_id: form.department_id || null,
        employee_id: form.employee_id || null,
      })
      setSuccessMsg(`Manager created! Username: "${res.username}" — share password: "${form.password}"`)
      loadManagers()
    } catch (err: any) {
      setFormError(err?.message || 'Failed to create manager')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Managers</h1>
          <p className="text-xs text-gray-500 font-mono mt-0.5">
            All manager accounts · Direct reports and access control
          </p>
        </div>
        <button onClick={openModal} className="btn-primary text-xs">
          + Add Manager
        </button>
      </div>

      <div className="card">
        <div className="section-header">
          <div className="flex items-center gap-2">
            <UserIcon className="w-4 h-4 text-gray-400" />
            <span className="section-title">Manager directory</span>
          </div>
          <span className="badge badge-gray">{managers.length} managers</span>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-500">
            <SpinnerIcon className="w-4 h-4 animate-spin" /> Loading…
          </div>
        ) : error ? (
          <div className="m-4 border border-red-200 bg-red-50 text-red-700 text-xs px-3 py-2 flex items-center gap-1.5">
            <AlertIcon className="w-3.5 h-3.5" /> {error}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Full Name</th>
                  <th>Username</th>
                  <th>Email</th>
                  <th>Direct Reports</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {managers.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="text-center text-gray-400 py-8 text-sm">
                      No managers found.
                    </td>
                  </tr>
                ) : managers.map((m) => (
                  <tr key={m.id}>
                    <td className="font-medium text-gray-900">{m.full_name}</td>
                    <td className="font-mono text-xs text-gray-600">{m.username}</td>
                    <td className="text-gray-600 text-xs">{m.email || '—'}</td>
                    <td className="tabnum text-gray-700">{m.direct_reports_count}</td>
                    <td>
                      <span className={m.is_active ? 'badge badge-green' : 'badge badge-gray'}>
                        {m.is_active ? 'active' : 'inactive'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add Manager Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white border border-gray-200 rounded-none shadow-xl w-full max-w-md mx-4 p-6 flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-900">Add Manager Account</h2>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600 text-lg leading-none">&times;</button>
            </div>

            {successMsg ? (
              <div className="border border-green-200 bg-green-50 text-green-800 text-xs px-3 py-3 rounded">
                <p className="font-semibold mb-1">✓ Manager created</p>
                <p>{successMsg}</p>
                <button onClick={() => setShowModal(false)} className="mt-3 btn-primary text-xs">Close</button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="flex flex-col gap-3">
                {formError && (
                  <div className="text-xs text-red-600 border border-red-200 bg-red-50 px-3 py-2 rounded">
                    {formError}
                  </div>
                )}
                {[
                  { label: 'Full Name', name: 'full_name', required: true, placeholder: 'Ion Popescu' },
                  { label: 'Email', name: 'email', required: true, type: 'email', placeholder: 'i.popescu@company.com' },
                  { label: 'Username', name: 'username', required: true, placeholder: 'i.popescu' },
                  { label: 'Password', name: 'password', required: true, placeholder: 'Temporary password' },
                ].map(({ label, name, required, type, placeholder }) => (
                  <div key={name} className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-gray-700">{label}{required && ' *'}</label>
                    <input
                      type={type || 'text'}
                      name={name}
                      value={(form as any)[name]}
                      onChange={handleChange}
                      required={required}
                      placeholder={placeholder}
                      className="w-full px-3 py-1.5 text-xs border border-gray-200 bg-gray-50 focus:outline-none focus:ring-1 focus:ring-gray-400"
                    />
                  </div>
                ))}
                <div className="flex items-center gap-2 pt-2">
                  <button type="submit" disabled={submitting} className="btn-primary text-xs flex-1">
                    {submitting ? 'Creating…' : 'Create Manager'}
                  </button>
                  <button type="button" onClick={() => setShowModal(false)} className="btn-ghost text-xs flex-1">
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
